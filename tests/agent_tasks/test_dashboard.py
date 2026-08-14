"""Tests for task dashboard read models."""

from __future__ import annotations

import uuid

from omnigent.agent_tasks.dashboard import build_task_dashboard
from omnigent.agent_tasks.executions import start_execution_for_item
from omnigent.agent_tasks.role_keys import WORKER_DEFAULT_ROLE_KEY
from omnigent.db.utils import now_epoch
from omnigent.stores.task_asset_store.sqlalchemy_store import SqlAlchemyTaskAssetStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def test_inbox_only_unassigned_awaiting_ack(db_uri: str) -> None:
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    task_id = _uid("task_inbox")
    task_store.create(
        task_id,
        "Demo task",
        state="active",
        manager_conversation_id=_uid("mgr_conv"),
    )
    task = task_store.get(task_id)
    assert task is not None

    item_store.create_item(
        _uid("unassigned"),
        task_id,
        "Pick a worker",
        state="pending",
        instructions="No worker yet",
    )
    worker = worker_store.create_worker(
        _uid("worker_slot"),
        task_id,
        role_key=WORKER_DEFAULT_ROLE_KEY,
    )
    item_store.create_item(
        _uid("assigned"),
        task_id,
        "Assigned proposal",
        state="pending",
        instructions="Already routed",
        worker_id=worker.id,
    )

    dashboard = build_task_dashboard(task, event_store, item_store, worker_store)
    assert len(dashboard["inbox_items"]) == 1
    assert dashboard["inbox_items"][0]["title"] == "Pick a worker"


def test_dashboard_includes_task_assets(db_uri: str) -> None:
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    asset_store = SqlAlchemyTaskAssetStore(db_uri)
    task_id = _uid("task_assets")
    task_store.create(
        task_id,
        "Asset task",
        state="active",
        manager_conversation_id=_uid("mgr_conv_assets"),
    )
    task = task_store.get(task_id)
    assert task is not None

    asset_store.create_asset(
        task_id,
        kind="url",
        title="PR #42",
        url="https://example.com/pr/42",
    )

    dashboard = build_task_dashboard(
        task,
        event_store,
        item_store,
        worker_store,
        asset_store,
    )
    assert len(dashboard["assets"]) == 1
    assert dashboard["assets"][0]["title"] == "PR #42"
    assert dashboard["assets"][0]["url"] == "https://example.com/pr/42"


def test_worker_lane_rows_and_state(db_uri: str) -> None:
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    task_id = _uid("task_lane")
    task_store.create(
        task_id,
        "Lane task",
        state="active",
        manager_conversation_id=_uid("mgr_conv2"),
    )
    task = task_store.get(task_id)
    assert task is not None

    worker = worker_store.create_worker(
        _uid("worker_lane"),
        task_id,
        role_key=WORKER_DEFAULT_ROLE_KEY,
    )
    running_item = item_store.create_item(
        _uid("running_item"),
        task_id,
        "Fix CI",
        state="running",
        instructions="Investigate",
        worker_id=worker.id,
    )
    queued_item = item_store.create_item(
        _uid("queued_item"),
        task_id,
        "Retry tests",
        state="queued",
        instructions="Re-run suite",
        worker_id=worker.id,
    )
    done_item = item_store.create_item(
        _uid("done_item"),
        task_id,
        "Old fix",
        state="done",
        instructions="Completed earlier",
        worker_id=worker.id,
    )

    start_execution_for_item(
        task=task,
        item=running_item,
        task_event_store=event_store,
        conversation_id=_uid("worker_conv"),
        status="running",
    )
    done_execution = start_execution_for_item(
        task=task,
        item=done_item,
        task_event_store=event_store,
        conversation_id=_uid("worker_conv_done"),
        status="succeeded",
    )
    event_store.update_execution(
        done_execution.id,
        finished_at=now_epoch() - 50,
    )

    dashboard = build_task_dashboard(task, event_store, item_store, worker_store)
    assert len(dashboard["workers"]) == 1
    lane = dashboard["workers"][0]
    assert lane["state"] == "active"
    assert lane["situation"].startswith("Running:")
    kinds = [row["kind"] for row in lane["rows"]]
    assert "execution" in kinds
    assert "item" in kinds
    folded = [row["default_folded"] for row in lane["rows"]]
    assert False in folded
    assert True in folded
    assert queued_item.title in {
        row["item"]["title"] for row in lane["rows"] if row["kind"] == "item"
    }
    # Finished work stays on the execution history row, not as a task-item row.
    done_titles = {row["item"]["title"] for row in lane["rows"] if row["kind"] == "item"}
    assert done_item.title not in done_titles
