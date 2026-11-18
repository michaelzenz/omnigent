"""Tests for the subscriptions of GlobalHub managers to the sessions of their workers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from omnigent.agent_tasks.board_stores import BoardStores, open_board_stores
from omnigent.agent_tasks.worker_subscriptions import follow_workers, unfollow_workers
from omnigent.entities import SESSION_EVENT_SOURCE, Task

ALICE = "alice@example.com"
PR = "https://example.com/example-org/repo/pull/12"


@pytest.fixture()
def board(db_uri: str) -> BoardStores:
    stores = open_board_stores(SimpleNamespace(storage_location=db_uri))
    assert stores is not None
    return stores


def _manager(board: BoardStores, conversation_id: str | None) -> str:
    return board.managers.create(
        uuid.uuid4().hex, owner_user_id=ALICE, title="Storage", conversation_id=conversation_id
    ).id


def _task(
    board: BoardStores,
    *worker_sessions: str,
    manager_id: str | None = None,
    state: str = "active",
    title: str = "S3 retries",
) -> Task:
    task = board.tasks.create(
        uuid.uuid4().hex, owner_user_id=ALICE, title=title, manager_id=manager_id, state=state
    )
    for session_id in worker_sessions:
        board.workers.create(
            uuid.uuid4().hex, owner_user_id=ALICE, task_id=task.id, session_id=session_id
        )
    return task


def _followed(board: BoardStores, session_id: str) -> dict[str, tuple[str, str | None]]:
    return {
        subscription.source_key: (subscription.source_type, subscription.note)
        for subscription in board.subscriptions.list_for_session(session_id, owner_user_id=ALICE)
    }


def test_a_manager_follows_the_worker_sessions_of_its_task_but_not_its_own(
    board: BoardStores,
) -> None:
    manager_session, first_worker, second_worker, later_worker = (
        uuid.uuid4().hex for _ in range(4)
    )
    manager_id = _manager(board, manager_session)
    task = _task(board, first_worker, second_worker, manager_session, manager_id=manager_id)

    follow_workers(board, task, owner_user_id=ALICE)
    follow_workers(board, task, owner_user_id=ALICE)
    follow_workers(board, task, owner_user_id=ALICE, session_ids=[later_worker, later_worker])

    worker = (SESSION_EVENT_SOURCE, "Worker on the task S3 retries")
    assert _followed(board, manager_session) == {
        first_worker: worker,
        second_worker: worker,
        later_worker: worker,
    }
    assert board.subscriptions.subscribers(owner_user_id=ALICE, source_key=first_worker) == [
        manager_session
    ]
    assert board.subscriptions.subscribers(owner_user_id=ALICE, source_key=manager_session) == []


def test_following_changes_nothing_without_a_manager_session(board: BoardStores) -> None:
    worker_session = uuid.uuid4().hex
    tasks = [
        _task(board, worker_session),
        _task(board, worker_session, manager_id=_manager(board, None)),
        _task(board, worker_session, manager_id=uuid.uuid4().hex),
    ]

    for task in tasks:
        follow_workers(board, task, owner_user_id=ALICE)

    assert board.subscriptions.list_for_types([SESSION_EVENT_SOURCE], owner_user_id=ALICE) == []


def test_unfollowing_a_task_keeps_the_workers_of_the_managers_other_live_tasks(
    board: BoardStores,
) -> None:
    manager_session, shared, only_here, shared_with_archived = (uuid.uuid4().hex for _ in range(4))
    manager_id = _manager(board, manager_session)
    sessionless_manager_id = _manager(board, None)
    leaving = _task(board, shared, only_here, shared_with_archived, manager_id=manager_id)
    _task(board, shared, manager_id=manager_id, state="idle", title="Docs page")
    _task(board, shared_with_archived, manager_id=manager_id, state="archived", title="Old work")
    follow_workers(board, leaving, owner_user_id=ALICE)
    board.subscriptions.subscribe(
        owner_user_id=ALICE,
        session_id=manager_session,
        source_type="github.pull_request",
        source_key=PR,
    )

    unfollow_workers(board, leaving, owner_user_id=ALICE, manager_id=None)
    unfollow_workers(board, leaving, owner_user_id=ALICE, manager_id=sessionless_manager_id)
    untouched = set(_followed(board, manager_session))
    unfollow_workers(board, leaving, owner_user_id=ALICE, manager_id=manager_id)

    assert untouched == {shared, only_here, shared_with_archived, PR}
    assert set(_followed(board, manager_session)) == {shared, PR}
