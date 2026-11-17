"""Tests for the store of the GlobalHub board search vectors, on the scoring path in Python."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import select

from omnigent.agent_tasks.embeddings import EMBEDDING_DIMENSIONS, unit_vector
from omnigent.db.db_models import SqlTaskEmbedding, workspace_scope
from omnigent.db.utils import get_or_create_engine
from omnigent.stores.task_embedding_store import SourceVector, StaleSource
from omnigent.stores.task_embedding_store import sqlalchemy_store as embedding_tables
from omnigent.stores.task_embedding_store.sqlalchemy_store import SqlAlchemyTaskEmbeddingStore
from omnigent.stores.task_store import sqlalchemy_store as task_tables
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
MODEL = "test-embedder"
NOW = 1_800_000_000
SESSION = f"{200:032x}"
OTHER_SESSION = f"{201:032x}"
GONE_SESSION = f"{202:032x}"


def _vector(*leading: float) -> list[float]:
    return unit_vector([*leading, *[0.0] * (EMBEDDING_DIMENSIONS - len(leading))])


class _Board:
    """Tasks, workers and search vectors over real stores, with one settable clock."""

    def __init__(self, db_uri: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.db_uri = db_uri
        self.tasks = SqlAlchemyTaskStore(db_uri)
        self.workers = SqlAlchemyWorkerStore(db_uri)
        self.embeddings = SqlAlchemyTaskEmbeddingStore(db_uri)
        self.now = NOW
        self.session_times: dict[str, int] = {}
        monkeypatch.setattr(task_tables, "now_epoch", lambda: self.now)
        monkeypatch.setattr(embedding_tables, "now_epoch", lambda: self.now)

    def task(self, title: str = "Task", owner: str = ALICE, **fields: Any) -> str:
        return self.tasks.create(uuid.uuid4().hex, owner_user_id=owner, title=title, **fields).id

    def worker(self, task_id: str, session_id: str, owner: str = ALICE) -> str:
        return self.workers.create(
            uuid.uuid4().hex, owner_user_id=owner, task_id=task_id, session_id=session_id
        ).id

    def _session_changes(self, workspace_id: int, session_ids: Sequence[str]) -> dict[str, int]:
        del workspace_id
        return {
            session_id: self.session_times[session_id]
            for session_id in session_ids
            if session_id in self.session_times
        }

    def stale(self, model: str = MODEL) -> dict[str, StaleSource]:
        found = self.embeddings.stale_sources_all_workspaces(
            model=model, limit=100, session_changes=self._session_changes
        )
        return {source.source_id: source for source in found}

    def save(self, source_id: str, vector: list[float], digest: str = "digest") -> None:
        source = self.stale()[source_id]
        self.embeddings.save_all(
            [SourceVector(source=source, content_sha256=digest, vector=vector)], model=MODEL
        )

    def stored(self) -> set[str]:
        with get_or_create_engine(self.db_uri).connect() as connection:
            return set(connection.execute(select(SqlTaskEmbedding.source_id)).scalars())


@pytest.fixture()
def board(db_uri: str, monkeypatch: pytest.MonkeyPatch) -> _Board:
    return _Board(db_uri, monkeypatch)


def test_a_task_or_session_is_stale_until_read_after_its_last_change(board: _Board) -> None:
    task_id = board.task("Fix the retries", description="S3 uploads time out")
    other_task = board.task("Rotate the keys")
    archived = board.task("Archived", state="archived")
    board.worker(task_id, SESSION)
    board.worker(other_task, SESSION)
    board.worker(archived, OTHER_SESSION)
    board.worker(task_id, GONE_SESSION)
    with workspace_scope(7):
        elsewhere = board.task("Elsewhere")
    board.session_times.update({SESSION: NOW, OTHER_SESSION: NOW})

    first = board.stale()
    board.save(SESSION, _vector(1.0))
    within_the_second = board.stale()
    board.now += 1
    after_the_second = board.stale()
    board.save(SESSION, _vector(1.0))
    current = board.stale()
    board.session_times[SESSION] = NOW + 1
    changed = board.stale()
    board.embeddings.touch_all([changed[SESSION]])
    board.now += 1
    board.embeddings.touch_all([board.stale()[SESSION]])
    other_model = board.stale(model="another-embedder")

    assert set(first) == {task_id, other_task, elsewhere, SESSION}
    assert (first[SESSION].source, first[task_id].source, first[elsewhere].workspace_id) == (
        "session",
        "task",
        7,
    )
    assert (first[task_id].title, first[task_id].description) == (
        "Fix the retries",
        "S3 uploads time out",
    )
    assert {source.needs_vector for source in first.values()} == {True}
    assert (SESSION in within_the_second, SESSION in after_the_second) == (False, True)
    assert SESSION not in current
    assert (changed[SESSION].content_sha256, changed[SESSION].needs_vector) == ("digest", False)
    assert SESSION not in board.stale()
    assert other_model[SESSION].needs_vector is True


def test_saving_skips_deleted_sources_and_each_pass_deletes_orphan_vectors(
    board: _Board,
) -> None:
    kept, dropped = board.task("Kept"), board.task("Dropped")
    session_worker = board.worker(kept, SESSION)
    board.worker(dropped, OTHER_SESSION)
    board.session_times.update({SESSION: NOW, OTHER_SESSION: NOW})
    stale = board.stale()

    board.tasks.delete(dropped, owner_user_id=ALICE)
    board.embeddings.save_all(
        [
            SourceVector(source=stale[source_id], content_sha256="digest", vector=_vector(1.0))
            for source_id in (kept, dropped, SESSION, OTHER_SESSION)
        ],
        model=MODEL,
    )
    saved = board.stored()
    board.workers.delete(session_worker, owner_user_id=ALICE)
    board.embeddings.delete_orphans_all_workspaces()
    without_its_worker = board.stored()
    board.tasks.delete(kept, owner_user_id=ALICE)
    board.embeddings.delete_orphans_all_workspaces()

    assert saved == {kept, SESSION}
    assert without_its_worker == {kept}
    assert board.stored() == set()


def test_task_scores_hold_each_live_tasks_title_and_sessions_for_the_owner_in_the_workspace(
    board: _Board,
) -> None:
    query = _vector(1.0)
    near_title, near_session = board.task("Near title"), board.task("Near session")
    shares_the_session, archived = board.task("Shares the session"), board.task("Archived")
    bobs = board.task("Bob's", owner=BOB)
    with workspace_scope(7):
        elsewhere = board.task("Elsewhere")
    on_near_session = board.worker(near_session, SESSION)
    on_shared = board.worker(shares_the_session, SESSION)
    board.session_times[SESSION] = NOW
    board.now += 1
    for source_id, vector in [
        (near_title, _vector(0.8, 0.6)),
        (near_session, _vector(0.3, 0.95)),
        (SESSION, _vector(0.95, 0.3)),
        (archived, query),
        (bobs, query),
        (elsewhere, query),
    ]:
        board.save(source_id, vector)
    board.tasks.update(archived, owner_user_id=ALICE, state="archived")

    def scores(owner: str = ALICE, model: str = MODEL) -> dict[str, Any]:
        return board.embeddings.task_scores(owner_user_id=owner, vector=query, model=model)

    alices = scores()
    with workspace_scope(7):
        in_workspace = scores()

    assert set(alices) == {near_title, near_session, shares_the_session}
    assert (round(alices[near_title].score, 2), alices[near_title].workers) == (0.8, ())
    assert round(alices[near_session].title_score, 2) == 0.3
    assert [
        (worker.worker_id, worker.session_id, round(worker.score, 2))
        for task_id in (near_session, shares_the_session)
        for worker in alices[task_id].workers
    ] == [(on_near_session, SESSION, 0.95), (on_shared, SESSION, 0.95)]
    assert (alices[shares_the_session].title_score, round(alices[near_session].score, 2)) == (
        None,
        0.95,
    )
    assert set(scores(BOB)) == {bobs}
    assert set(in_workspace) == {elsewhere}
    assert scores(model="another-embedder") == {}
