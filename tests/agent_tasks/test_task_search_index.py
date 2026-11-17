"""Tests for the background embedding of GlobalHub tasks and sessions, and of search queries."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest

from omnigent.agent_tasks.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingError,
    task_embedding_text,
    unit_vector,
)
from omnigent.agent_tasks.task_search_index import QUERY_PAUSE_S, REFRESH_PAUSE_S, TaskSearchIndex
from omnigent.stores.task_embedding_store import sqlalchemy_store as embedding_tables
from omnigent.stores.task_embedding_store.sqlalchemy_store import SqlAlchemyTaskEmbeddingStore
from omnigent.stores.task_store import sqlalchemy_store as task_tables
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
NOW = 1_800_000_000
SESSION = f"{200:032x}"
QUIET_SESSION = f"{201:032x}"


class _Embedder:
    """Embeds every text as the same vector and records each call; it fails while ``failing``."""

    model = "test-embedder"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.failing = False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.failing:
            raise EmbeddingError("the endpoint answered 429")
        return [unit_vector([1.0, *[0.0] * (EMBEDDING_DIMENSIONS - 1)]) for _ in texts]


class _Sessions:
    """The change time and the recent turns of each session, as the test sets them."""

    def __init__(self) -> None:
        self.times: dict[str, int] = {}
        self.turns: dict[str, str] = {}

    def changed_at(self, workspace_id: int, session_ids: Sequence[str]) -> dict[str, int]:
        del workspace_id
        return {session: self.times[session] for session in session_ids if session in self.times}

    def recent_turns(self, workspace_id: int, session_id: str) -> str:
        del workspace_id
        return self.turns.get(session_id, "")


class _Search:
    """A search index over real stores, with one settable clock for the stores and the index."""

    def __init__(self, db_uri: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = NOW
        monkeypatch.setattr(task_tables, "now_epoch", lambda: self.now)
        monkeypatch.setattr(embedding_tables, "now_epoch", lambda: self.now)
        self.tasks = SqlAlchemyTaskStore(db_uri)
        self.workers = SqlAlchemyWorkerStore(db_uri)
        self.sessions = _Sessions()
        self.embedder = _Embedder()
        self.index = TaskSearchIndex(
            SqlAlchemyTaskEmbeddingStore(db_uri),
            self.embedder,
            self.sessions,
            min_score=0.5,
            clock=lambda: self.now,
        )

    def task(self, title: str, description: str | None = None) -> str:
        return self.tasks.create(
            uuid.uuid4().hex, owner_user_id=ALICE, title=title, description=description
        ).id

    def worker(self, task_id: str, session_id: str) -> None:
        self.workers.create(
            uuid.uuid4().hex, owner_user_id=ALICE, task_id=task_id, session_id=session_id
        )


@pytest.fixture()
def search(db_uri: str, monkeypatch: pytest.MonkeyPatch) -> _Search:
    return _Search(db_uri, monkeypatch)


async def test_a_refresh_embeds_new_and_changed_texts_and_keeps_the_vectors_of_unchanged_ones(
    search: _Search,
) -> None:
    fix = search.task("Fix the retries", "S3 uploads time out")
    search.worker(fix, SESSION)
    search.worker(fix, QUIET_SESSION)
    search.sessions.times.update({SESSION: NOW, QUIET_SESSION: NOW})
    search.sessions.turns[SESSION] = "User: babysit the flag rollout"
    search.now += 1
    read = await search.index.refresh()
    again = await search.index.refresh()
    search.now += 1
    search.tasks.update(fix, owner_user_id=ALICE, state="idle")
    search.sessions.times[QUIET_SESSION] = search.now
    search.now += 1
    after_unchanged_texts = await search.index.refresh()
    search.sessions.times[SESSION] = search.now
    search.sessions.turns[SESSION] = "User: babysit the flag rollout\n\nAgent: Watching it"
    search.now += 1
    after_a_new_turn = await search.index.refresh()

    assert (read, again, after_unchanged_texts, after_a_new_turn) == (3, 0, 2, 1)
    assert [sorted(call) for call in search.embedder.calls] == [
        sorted(
            [
                task_embedding_text("Fix the retries", "S3 uploads time out"),
                "User: babysit the flag rollout",
            ]
        ),
        ["User: babysit the flag rollout\n\nAgent: Watching it"],
    ]


async def test_a_failed_embedding_pauses_the_refresh(search: _Search) -> None:
    search.task("Fix the retries")
    search.now += 1
    search.embedder.failing = True
    failed = await search.index.refresh()
    search.embedder.failing = False
    search.now += REFRESH_PAUSE_S - 1
    paused = await search.index.refresh()
    search.now += 1
    resumed = await search.index.refresh()

    assert (failed, paused, resumed) == (0, 0, 1)
    assert len(search.embedder.calls) == 2


async def test_a_query_is_embedded_once_per_owner_and_a_failure_pauses_query_embedding(
    search: _Search,
) -> None:
    async def vector(query: str, owner: str = ALICE) -> list[float] | None:
        return await search.index.query_vector(query, owner_user_id=owner)

    first = await vector("  the flaky S3 thing ")
    cached = await vector("the flaky S3 thing")
    bobs = await vector("the flaky S3 thing", BOB)
    search.embedder.failing = True
    failed = await vector("table summary rollout")
    search.embedder.failing = False
    search.now += QUERY_PAUSE_S - 1
    paused = await vector("table summary rollout")
    cached_while_paused = await vector("the flaky S3 thing")
    search.now += 1
    resumed = await vector("table summary rollout")
    blank = await vector("   ")

    assert first is not None
    assert cached == bobs == cached_while_paused == first
    assert (failed, paused, blank) == (None, None, None)
    assert resumed is not None
    assert search.embedder.calls == [
        ["the flaky S3 thing"],
        ["the flaky S3 thing"],
        ["table summary rollout"],
        ["table summary rollout"],
    ]
