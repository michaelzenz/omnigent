"""Keep the vectors of the GlobalHub board search current, and score the tasks against a query.

A task has a vector of its own title and overview, and each session that works
on a live task has a vector of its recent turns. A background pass embeds the
tasks and sessions of every workspace whose text changed. A search embeds the
query once for its owner, and scores only the owner's live tasks in the
current workspace. When the embedding endpoint fails, the search uses keywords
only for a short time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from omnigent.agent_tasks.embeddings import (
    Embedder,
    EmbeddingError,
    task_embedding_text,
    text_sha256,
)
from omnigent.db.db_models import current_workspace_id
from omnigent.stores.task_embedding_store import (
    SourceVector,
    StaleSource,
    TaskEmbeddingStore,
    TaskVectorScores,
)

_logger = logging.getLogger(__name__)

_REFRESH_BATCH = 16
_MAX_REFRESH_SOURCES = 256
_QUERY_CACHE_SIZE = 256
_QUERY_TIMEOUT_S = 3.0
REFRESH_PAUSE_S = 5 * 60
QUERY_PAUSE_S = 60


class WorkerSessions(Protocol):
    """Reads the sessions that work on tasks, in the workspace of each session."""

    def changed_at(self, workspace_id: int, session_ids: Sequence[str]) -> Mapping[str, int]:
        """Return the ``updated_at`` of each session that still exists, by session id."""
        ...

    def recent_turns(self, workspace_id: int, session_id: str) -> str:
        """Return the text of the session's last turns, the oldest turn first."""
        ...


class TaskSearchIndex:
    """Embeds the tasks and sessions of every workspace, and scores each owner's tasks."""

    def __init__(
        self,
        store: TaskEmbeddingStore,
        embedder: Embedder,
        sessions: WorkerSessions,
        *,
        min_score: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        :param store: The vectors of the tasks and sessions.
        :param embedder: Embeds the texts and the queries.
        :param sessions: Reads the change times and the turns of the sessions.
        :param min_score: The least similarity that makes a task, a title or a
            worker a match.
        :param clock: Returns seconds, for the pauses after a failure.
        """
        self._store = store
        self._embedder = embedder
        self._sessions = sessions
        self.min_score = min_score
        self._clock = clock
        self._refresh_lock = asyncio.Lock()
        self._refresh_paused_until = 0.0
        self._query_paused_until = 0.0
        self._query_vectors: OrderedDict[tuple[int, str | None, str], list[float]] = OrderedDict()

    async def refresh(
        self, *, max_sources: int = _MAX_REFRESH_SOURCES, batch_size: int = _REFRESH_BATCH
    ) -> int:
        """
        Embed the live tasks and their sessions whose vector is missing or stale.

        The pass first deletes the vectors of deleted tasks and of sessions that
        work on no task. A source whose text did not change keeps its vector.
        When an embedding call or the store fails, the pass stops, and no pass
        starts for :data:`REFRESH_PAUSE_S` seconds. A pass does not start while
        another pass runs.

        :returns: How many stale tasks and sessions the pass read.
        """
        if self._refresh_lock.locked() or self._clock() < self._refresh_paused_until:
            return 0
        async with self._refresh_lock:
            read = 0
            try:
                await asyncio.to_thread(self._store.delete_orphans_all_workspaces)
                stale = await asyncio.to_thread(
                    self._store.stale_sources_all_workspaces,
                    model=self._embedder.model,
                    limit=max_sources,
                    session_changes=self._sessions.changed_at,
                )
                for start in range(0, len(stale), batch_size):
                    batch = stale[start : start + batch_size]
                    if not await self._refresh_batch(batch):
                        break
                    read += len(batch)
            except Exception:
                self._refresh_paused_until = self._clock() + REFRESH_PAUSE_S
                _logger.exception("The board search could not refresh its vectors")
            return read

    async def _refresh_batch(self, stale: list[StaleSource]) -> bool:
        """Embed the sources whose text changed and keep the others; return whether it worked."""
        texts = await asyncio.to_thread(self._texts, stale)
        digests = [text_sha256(text) for text in texts]
        changed = [
            (source, text, digest)
            for source, text, digest in zip(stale, texts, digests, strict=True)
            if source.needs_vector or source.content_sha256 != digest
        ]
        unchanged = [
            source
            for source, digest in zip(stale, digests, strict=True)
            if not source.needs_vector and source.content_sha256 == digest
        ]
        with_text = [(source, text, digest) for source, text, digest in changed if text]
        vectors: list[list[float]] = []
        if with_text:
            try:
                vectors = await self._embedder.embed([text for _, text, _ in with_text])
            except EmbeddingError as exc:
                self._refresh_paused_until = self._clock() + REFRESH_PAUSE_S
                _logger.warning(
                    "The board search could not embed %d texts: %s", len(with_text), exc
                )
                return False
        saved = [
            SourceVector(source=source, content_sha256=digest, vector=vector)
            for (source, _, digest), vector in zip(with_text, vectors, strict=True)
        ]
        saved.extend(
            SourceVector(source=source, content_sha256=digest, vector=[])
            for source, text, digest in changed
            if not text
        )
        if saved:
            await asyncio.to_thread(self._store.save_all, saved, model=self._embedder.model)
        if unchanged:
            await asyncio.to_thread(self._store.touch_all, unchanged)
        return True

    def _texts(self, sources: Sequence[StaleSource]) -> list[str]:
        """Return the text of each source: a task's title and overview, or a session's turns."""
        return [
            task_embedding_text(source.title, source.description)
            if source.source == "task"
            else self._sessions.recent_turns(source.workspace_id, source.source_id)
            for source in sources
        ]

    async def query_vector(self, query: str, *, owner_user_id: str | None) -> list[float] | None:
        """
        Return the vector of the owner's search query in the current workspace.

        Each owner's queries are cached apart, so a fast answer does not show
        that another user searched the same text.

        :returns: ``None`` when the embedding call fails or takes longer than
            :data:`_QUERY_TIMEOUT_S` seconds; the search then uses keywords only,
            and no query is embedded for :data:`QUERY_PAUSE_S` seconds.
        """
        text = query.strip()
        if not text:
            return None
        key = (current_workspace_id(), owner_user_id, text)
        cached = self._query_vectors.get(key)
        if cached is not None:
            self._query_vectors.move_to_end(key)
            return cached
        if self._clock() < self._query_paused_until:
            return None
        try:
            vectors = await asyncio.wait_for(self._embedder.embed([text]), _QUERY_TIMEOUT_S)
        except (EmbeddingError, TimeoutError) as exc:
            self._query_paused_until = self._clock() + QUERY_PAUSE_S
            _logger.warning("The board search could not embed a query: %r", exc)
            return None
        except Exception:
            self._query_paused_until = self._clock() + QUERY_PAUSE_S
            _logger.exception("The board search could not embed a query")
            return None
        vector = vectors[0]
        self._query_vectors[key] = vector
        while len(self._query_vectors) > _QUERY_CACHE_SIZE:
            self._query_vectors.popitem(last=False)
        return vector

    def task_scores(
        self, *, owner_user_id: str | None, vector: list[float]
    ) -> dict[str, TaskVectorScores]:
        """Return how near each of the owner's live tasks in the workspace is to ``vector``."""
        return self._store.task_scores(
            owner_user_id=owner_user_id, vector=vector, model=self._embedder.model
        )
