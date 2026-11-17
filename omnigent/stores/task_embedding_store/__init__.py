"""Persistence for the vectors of each user's GlobalHub board search.

A task has a vector of its own title and overview, and each session that works
on a live task has a vector of its recent turns. A session that works on
several tasks has one vector, which counts for each of those tasks. Every
search reads only the owner's vectors in the current workspace, and only those
of live tasks and of the sessions that work on them. The background pass that
keeps the vectors current runs outside a request, so it reads and writes every
workspace.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

SessionChanges = Callable[[int, Sequence[str]], Mapping[str, int]]
SourceKind = Literal["task", "session"]


@dataclass(frozen=True)
class StaleSource:
    """A task or a session whose text changed after the server last read it, or with no vector.

    :param workspace_id: The workspace of the source.
    :param source: ``"task"`` for a task's title and overview, or ``"session"``
        for the recent turns of a session that works on a live task.
    :param source_id: The task or the session.
    :param owner_user_id: The owner, or ``None`` in single-user mode.
    :param changed_at: The change time of the source: the task's
        ``updated_at`` (else its ``created_at``), or the session's ``updated_at``.
    :param read_at: When the server read the source, in Unix epoch seconds.
    :param content_sha256: The hash of the text that the stored vector came
        from, or ``None`` without a stored vector.
    :param needs_vector: Whether the source needs a new vector even when its
        text did not change: it has no vector, the vector is from another
        model, or the database vector column is empty.
    :param title: The task's title, for a task.
    :param description: The task's overview, for a task.
    """

    workspace_id: int
    source: SourceKind
    source_id: str
    owner_user_id: str | None
    changed_at: int
    read_at: int
    content_sha256: str | None
    needs_vector: bool
    title: str = ""
    description: str | None = None


@dataclass(frozen=True)
class SourceVector:
    """A new vector for a task or a session.

    :param source: The stale source that the vector is for.
    :param content_sha256: The hash of the embedded text.
    :param vector: The unit vector, or an empty one when the source has no text.
    """

    source: StaleSource
    content_sha256: str
    vector: Sequence[float]


@dataclass(frozen=True)
class WorkerScore:
    """How near the session of one of a task's workers is to a query.

    :param worker_id: The worker.
    :param session_id: The worker's session.
    :param score: The similarity of the session's recent turns.
    """

    worker_id: str
    session_id: str
    score: float


@dataclass(frozen=True)
class TaskVectorScores:
    """How near a live task is to a query vector.

    :param task_id: The task.
    :param title_score: The similarity of its own title and overview, or
        ``None`` without a vector.
    :param workers: Its workers whose session has a vector, nearest first.
    """

    task_id: str
    title_score: float | None
    workers: tuple[WorkerScore, ...]

    @property
    def score(self) -> float:
        """The similarity of the task's nearest vector."""
        scores = [worker.score for worker in self.workers]
        if self.title_score is not None:
            scores.append(self.title_score)
        return max(scores, default=0.0)


class TaskEmbeddingStore(ABC):
    """Abstract base for the persistence of the vectors of the GlobalHub board search."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def stale_sources_all_workspaces(
        self, *, model: str, limit: int, session_changes: SessionChanges
    ) -> builtins.list[StaleSource]:
        """
        Return up to ``limit`` live tasks and sessions, in every workspace, with a stale vector.

        A vector is stale when it is missing, from another model than ``model``,
        or read from its source before the source's last change. A read in the
        same second as the change it saw is stale once that second ends,
        because a later change in that second has the same change time. The
        most recently changed sources come first.

        :param session_changes: Given a workspace and session ids, returns the
            change time of each session that still exists, by session id.
        """

    @abstractmethod
    def save_all(self, vectors: Sequence[SourceVector], *, model: str) -> None:
        """
        Store each new vector, made with ``model``, for the change time of its source.

        A task deleted since the scan, or a session that no longer works on a
        task, gets no vector. An empty vector records a source with no text.
        """

    @abstractmethod
    def touch_all(self, sources: Sequence[StaleSource]) -> None:
        """Keep the vector of each source, whose text did not change, for its new change time."""

    @abstractmethod
    def delete_orphans_all_workspaces(self) -> None:
        """Delete the vectors of deleted tasks and of sessions that work on no task, everywhere."""

    @abstractmethod
    def task_scores(
        self, *, owner_user_id: str | None, vector: Sequence[float], model: str
    ) -> dict[str, TaskVectorScores]:
        """
        Return how near each of the owner's live tasks in the current workspace is to ``vector``.

        :returns: The scores by task id, for the tasks with at least one vector.
        """
