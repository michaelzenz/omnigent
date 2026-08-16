"""Tests for managed-task activity state sync."""

from __future__ import annotations

import uuid

from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def test_sync_sets_active_when_item_running(db_uri: str) -> None:
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    task_id = _uid("task_active")
    task_store.create(task_id, "Running task", "running goal", state="idle")
    task = task_store.get(task_id)
    assert task is not None
    item_store.create_item(_uid("item"), task_id, "Work", state="running")

    synced = sync_task_activity_state(task, task_store=task_store, task_item_store=item_store)
    assert synced.state == "active"


def test_sync_sets_idle_when_no_running_items(db_uri: str) -> None:
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    task_id = _uid("task_idle")
    task_store.create(task_id, "Idle task", "idle goal", state="active")
    task = task_store.get(task_id)
    assert task is not None
    item_store.create_item(_uid("queued"), task_id, "Queued", state="queued")
    item_store.create_item(_uid("done"), task_id, "Done", state="done")

    synced = sync_task_activity_state(task, task_store=task_store, task_item_store=item_store)
    assert synced.state == "idle"


def test_sync_skips_pending_and_archived(db_uri: str) -> None:
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    for state in ("pending", "archived"):
        task_id = _uid(f"task_{state}")
        task_store.create(task_id, f"{state} task", f"{state} goal", state=state)
        task = task_store.get(task_id)
        assert task is not None
        item_store.create_item(_uid(f"item_{state}"), task_id, "Work", state="running")
        synced = sync_task_activity_state(task, task_store=task_store, task_item_store=item_store)
        assert synced.state == state
