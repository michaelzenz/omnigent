"""Tests for the store of the keys that GlobalHub tasks follow."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omnigent.entities import SESSION_EVENT_SOURCE, TaskSubscription
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.task_subscription_store import Follower, SourceKeyError
from omnigent.stores.task_subscription_store.sqlalchemy_store import (
    SqlAlchemyTaskSubscriptionStore,
)
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
PR = "https://github.com/example-org/repo/pull/12"
SESSION_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


class _Board:
    """Tasks, workers and subscriptions over real stores."""

    def __init__(self, db_uri: str) -> None:
        self.tasks = SqlAlchemyTaskStore(db_uri)
        self.workers = SqlAlchemyWorkerStore(db_uri)
        self.subscriptions = SqlAlchemyTaskSubscriptionStore(db_uri)
        self.managers = SqlAlchemyManagerStore(db_uri)

    def task(self, owner: str = ALICE, **fields: Any) -> str:
        return self.tasks.create(uuid.uuid4().hex, owner_user_id=owner, title="Task", **fields).id

    def subscribe(
        self, task_id: str, key: str = PR, owner: str = ALICE, **fields: Any
    ) -> TaskSubscription:
        return self.subscriptions.subscribe(
            uuid.uuid4().hex, owner_user_id=owner, task_id=task_id, source_key=key, **fields
        )


@pytest.fixture()
def board(db_uri: str) -> _Board:
    return _Board(db_uri)


def test_a_task_follows_a_key_once_and_a_later_subscription_names_its_source(
    board: _Board,
) -> None:
    task_id = board.task()

    first = board.subscribe(task_id, PR)
    again = board.subscribe(task_id, PR, source="github")
    other_task = board.subscribe(board.task(), PR)

    assert (again.id, again.source) == (first.id, "github")
    assert other_task.id != first.id
    assert board.subscriptions.list_for_task(task_id, owner_user_id=ALICE) == [again]
    for unusable in ("", "k" * 513):
        with pytest.raises(SourceKeyError):
            board.subscribe(task_id, unusable)


def test_followers_are_live_tasks_by_subscription_and_by_worker_for_session_events(
    board: _Board,
) -> None:
    manager_id = board.managers.create(uuid.uuid4().hex, owner_user_id=ALICE, title="Storage").id
    subscribed = board.task(manager_id=manager_id)
    working = board.task(state="idle")
    both = board.task()
    archived = board.task(state="archived")
    bobs = board.task(owner=BOB)
    for task_id in (subscribed, both, archived):
        board.subscribe(task_id, SESSION_ID)
    board.subscribe(subscribed, PR)
    board.subscribe(bobs, SESSION_ID, owner=BOB)
    for task_id in (working, both):
        board.workers.create(
            uuid.uuid4().hex, owner_user_id=ALICE, task_id=task_id, session_id=SESSION_ID
        )

    def followers(source: str, key: str, owner: str = ALICE) -> list[Follower]:
        return board.subscriptions.followers(owner_user_id=owner, source=source, source_key=key)

    session_followers = followers(SESSION_EVENT_SOURCE, SESSION_ID)
    # The same key from another source does not reach the session's workers.
    other_followers = followers("other", SESSION_ID)

    assert len(session_followers) == 3
    assert set(session_followers) == {
        Follower(task_id=subscribed, manager_id=manager_id),
        Follower(task_id=working, manager_id=None),
        Follower(task_id=both, manager_id=None),
    }
    assert len(other_followers) == 2
    assert set(other_followers) == {
        Follower(task_id=subscribed, manager_id=manager_id),
        Follower(task_id=both, manager_id=None),
    }
    assert followers(SESSION_EVENT_SOURCE, SESSION_ID, owner=BOB) == [
        Follower(task_id=bobs, manager_id=None)
    ]
    assert followers("github", PR) == [Follower(task_id=subscribed, manager_id=manager_id)]
    # Keys match exactly: the agents write each key in one canonical form.
    assert followers("github", f"{PR}/files") == []


def test_the_live_list_and_deletes_are_owner_scoped(board: _Board) -> None:
    live, archived, bobs = board.task(), board.task(state="archived"), board.task(owner=BOB)
    followed = board.subscribe(live)
    board.subscribe(archived)
    board.subscribe(bobs, owner=BOB)

    listed = board.subscriptions.list_live(owner_user_id=ALICE)
    refused = [
        board.subscriptions.delete(followed.id, owner_user_id=BOB, task_id=live),
        board.subscriptions.delete(followed.id, owner_user_id=ALICE, task_id=archived),
    ]
    deleted = board.subscriptions.delete(followed.id, owner_user_id=ALICE, task_id=live)
    board.tasks.delete(archived, owner_user_id=ALICE)

    assert listed == [followed]
    assert (refused, deleted) == ([False, False], True)
    assert board.subscriptions.list_live(owner_user_id=ALICE) == []
    # Deleting a task deletes its subscriptions.
    assert board.subscriptions.list_for_task(archived, owner_user_id=ALICE) == []
