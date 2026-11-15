"""Tests for the GlobalHub task store."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omnigent.db.db_models import workspace_scope
from omnigent.stores.task_store import sqlalchemy_store as task_tables
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskStore:
    return SqlAlchemyTaskStore(db_uri)


def _create(
    store: SqlAlchemyTaskStore, title: str, *, owner: str | None = ALICE, **fields: Any
) -> str:
    task_id = uuid.uuid4().hex
    store.create(task_id, owner_user_id=owner, title=title, goal=f"Finish {title}", **fields)
    return task_id


def _board(store: SqlAlchemyTaskStore, owner: str | None = ALICE) -> list[str]:
    return [task.title for task in store.list(owner_user_id=owner)]


def _rank(store: SqlAlchemyTaskStore, task_id: str, owner: str | None) -> int:
    task = store.get(task_id, owner_user_id=owner)
    assert task is not None
    return task.queue_rank


def test_new_tasks_top_their_owners_board(store: SqlAlchemyTaskStore) -> None:
    first = _create(store, "first")
    _create(store, "second")
    _create(store, "bob's", owner=BOB)
    _create(store, "local", owner=None)

    assert _board(store) == ["second", "first"]
    assert _board(store, BOB) == ["bob's"]
    assert _board(store, None) == ["local"]
    task = store.get(first, owner_user_id=ALICE)
    assert task is not None and (task.state, task.goal) == ("active", "Finish first")
    assert store.get(first, owner_user_id=BOB) is None
    assert store.get(first, owner_user_id=None) is None


def test_list_and_count_filter_by_state_and_manager(store: SqlAlchemyTaskStore) -> None:
    manager_id = uuid.uuid4().hex
    _create(store, "managed", manager_id=manager_id)
    _create(store, "idle", state="idle")
    _create(store, "archived", state="archived")
    _create(store, "bob's", owner=BOB)

    def titles(**filters: Any) -> list[str]:
        return [task.title for task in store.list(owner_user_id=ALICE, **filters)]

    assert titles(states=["active"]) == ["managed"]
    assert titles(states=["idle", "archived"]) == ["archived", "idle"]
    assert titles(manager_id=manager_id) == ["managed"]
    assert titles(unmanaged=True) == ["archived", "idle"]
    assert store.count(owner_user_id=ALICE) == 3
    assert store.count(owner_user_id=ALICE, states=["active", "idle"], unmanaged=True) == 1
    assert store.count(owner_user_id=BOB, unmanaged=True) == 1


def test_update_changes_fields_and_tracks_resolution(store: SqlAlchemyTaskStore) -> None:
    manager_id = uuid.uuid4().hex
    task_id = _create(store, "task", manager_id=manager_id)

    unchanged = store.update(task_id, owner_user_id=ALICE, title="task")
    resolved = store.update(
        task_id, owner_user_id=ALICE, state="agent-resolved", description="Done."
    )
    revived = store.update(task_id, owner_user_id=ALICE, state="active", manager_id=None)

    assert unchanged is not None and unchanged.updated_at is None
    assert resolved is not None
    assert (resolved.state, resolved.description, resolved.manager_id) == (
        "agent-resolved",
        "Done.",
        manager_id,
    )
    assert resolved.resolved_at is not None and resolved.updated_at is not None
    assert revived is not None
    assert (revived.state, revived.resolved_at, revived.manager_id) == ("active", None, None)
    assert store.update(task_id, owner_user_id=BOB, title="taken") is None
    stored = store.get(task_id, owner_user_id=ALICE)
    assert stored is not None and stored.title == "task"


def test_move_to_board_end_keeps_live_tasks_above_resolved_ones(
    store: SqlAlchemyTaskStore,
) -> None:
    resolved = _create(store, "resolved")
    middle = _create(store, "middle")
    top = _create(store, "top")
    bob_task = _create(store, "bob's", owner=BOB)
    bob_rank = _rank(store, bob_task, BOB)
    store.update(resolved, owner_user_id=ALICE, state="agent-resolved")
    store.move_to_board_end(resolved, owner_user_id=ALICE)
    assert _board(store) == ["top", "middle", "resolved"]

    # A free rank sits just above the resolved task.
    store.move_to_board_end(top, owner_user_id=ALICE)
    assert _board(store) == ["middle", "top", "resolved"]

    # No free rank left: the resolved task shifts down to make room.
    store.move_to_board_end(middle, owner_user_id=ALICE)
    assert _board(store) == ["top", "middle", "resolved"]
    assert _rank(store, bob_task, BOB) == bob_rank


def test_work_tops_an_open_task_and_wakes_an_idle_one(store: SqlAlchemyTaskStore) -> None:
    resolved = _create(store, "resolved", state="agent-resolved")
    idle = _create(store, "idle", state="idle")
    _create(store, "newest")

    woken = store.record_work(idle, owner_user_id=ALICE)
    kept = store.record_work(resolved, owner_user_id=ALICE)

    assert woken is not None and (woken.state, woken.updated_at is not None) == ("active", True)
    assert kept is not None and (kept.state, kept.updated_at) == ("agent-resolved", None)
    assert _board(store) == ["idle", "newest", "resolved"]
    assert store.record_work(idle, owner_user_id=BOB) is None


def test_quiet_active_tasks_turn_idle_in_every_workspace_and_keep_their_place(
    store: SqlAlchemyTaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(task_tables, "now_epoch", lambda: 100)
    quiet = _create(store, "quiet")
    _create(store, "quiet resolved", state="agent-resolved")
    with workspace_scope(7):
        elsewhere = _create(store, "quiet elsewhere")
    monkeypatch.setattr(task_tables, "now_epoch", lambda: 900)
    recent = _create(store, "recent")
    order = _board(store)

    idled = store.idle_quiet_tasks_all_workspaces(changed_before=500)

    states = {
        title: task.state
        for title, task in (
            ("quiet", store.get(quiet, owner_user_id=ALICE)),
            ("recent", store.get(recent, owner_user_id=ALICE)),
        )
        if task is not None
    }
    with workspace_scope(7):
        other = store.get(elsewhere, owner_user_id=ALICE)
    assert (idled, states) == (2, {"quiet": "idle", "recent": "active"})
    assert other is not None and (other.state, other.updated_at) == ("idle", None)
    assert _board(store) == order


def test_board_moves_and_delete_are_owner_scoped(store: SqlAlchemyTaskStore) -> None:
    bottom = _create(store, "bottom")
    _create(store, "top")

    assert store.move_to_board_top(bottom, owner_user_id=BOB) is None
    store.move_to_board_top(bottom, owner_user_id=ALICE)
    assert _board(store) == ["bottom", "top"]
    # With no resolved tasks, the end is the very end.
    store.move_to_board_end(bottom, owner_user_id=ALICE)
    assert _board(store) == ["top", "bottom"]

    assert store.delete(bottom, owner_user_id=BOB) is False
    assert store.delete(bottom, owner_user_id=ALICE) is True
    assert _board(store) == ["top"]
