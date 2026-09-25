"""Tests for task dashboard read models."""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from omnigent.agent_tasks.dashboard import build_task_dashboard
from omnigent.agent_tasks.executions import start_execution_for_item
from omnigent.db.db_models import SqlConversation
from omnigent.db.utils import get_or_create_engine, now_epoch
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
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
        "demo goal",
        state="active",
        manager_id=_uid("mgr_conv"),
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
        "asset goal",
        state="active",
        manager_id=_uid("mgr_conv_assets"),
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
        "lane goal",
        state="active",
        manager_id=_uid("mgr_conv2"),
    )
    task = task_store.get(task_id)
    assert task is not None

    worker = worker_store.create_worker(
        _uid("worker_lane"),
        task_id,
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


def test_dashboard_excludes_deleted_workers(db_uri: str) -> None:
    """A worker soft-deleted with its session must not render a lane.

    Deleting a session soft-deletes (state='deleted') every worker bound
    to it via target_id. The dashboard is the task card's read model —
    a deleted lane can never dispatch again, so rendering it would
    resurrect a ghost lane for a session that no longer exists.
    """
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    task_id = _uid("task_deleted_lane")
    task_store.create(
        task_id,
        "Deleted lane task",
        "deleted lane goal",
        state="active",
        manager_id=_uid("mgr_conv_deleted"),
    )
    task = task_store.get(task_id)
    assert task is not None

    live = worker_store.create_worker(_uid("worker_live"), task_id, state="idle")
    doomed = worker_store.create_worker(
        _uid("worker_doomed"),
        task_id,
        target_id=_uid("deleted_session"),
        state="idle",
    )
    worker_store.update_worker(doomed.id, state="deleted")

    dashboard = build_task_dashboard(task, event_store, item_store, worker_store)
    lane_ids = {lane["worker_id"] for lane in dashboard["workers"]}
    assert live.id in lane_ids
    assert doomed.id not in lane_ids


def test_worker_lanes_sort_by_last_active(db_uri: str) -> None:
    """Lanes sort by the target session's last update within each state."""
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    task_id = _uid("task_sort_active")
    task_store.create(
        task_id,
        "Sort task",
        "sort goal",
        state="active",
        manager_id=_uid("mgr_sort"),
    )
    task = task_store.get(task_id)
    assert task is not None

    conv_old = conv_store.create_conversation(title="Old session")
    conv_new = conv_store.create_conversation(title="New session")
    # create_conversation stamps updated_at=now for both; pin distinct values
    # so the ordering is deterministic regardless of test speed. ORM update —
    # Uuid16 stores ids as blobs on SQLite, raw string SQL would not match.
    engine = get_or_create_engine(db_uri)
    with engine.begin() as conn:
        conn.execute(
            sa.update(SqlConversation)
            .where(SqlConversation.id == conv_old.id)
            .values(updated_at=1000000000)
        )
        conn.execute(
            sa.update(SqlConversation)
            .where(SqlConversation.id == conv_new.id)
            .values(updated_at=2000000000)
        )

    worker_old = worker_store.create_worker(
        _uid("w_old"),
        task_id,
        kind="internal",
        target_id=conv_old.id,
        state="idle",
    )
    worker_new = worker_store.create_worker(
        _uid("w_new"),
        task_id,
        kind="internal",
        target_id=conv_new.id,
        state="idle",
    )

    dashboard = build_task_dashboard(
        task,
        event_store,
        item_store,
        worker_store,
        conversation_store=conv_store,
    )
    lanes = dashboard["workers"]
    assert [lane["worker_id"] for lane in lanes] == [worker_new.id, worker_old.id]
    assert lanes[0]["last_active_at"] == 2000000000
    assert lanes[1]["last_active_at"] == 1000000000


def test_worker_lanes_last_active_falls_back_without_conversations(
    db_uri: str,
) -> None:
    """Without a conversation store, lanes still expose a fallback stamp."""
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    task_id = _uid("task_sort_fallback")
    task_store.create(
        task_id,
        "Fallback task",
        "fallback goal",
        state="active",
        manager_id=_uid("mgr_fallback"),
    )
    task = task_store.get(task_id)
    assert task is not None

    worker = worker_store.create_worker(_uid("w_fb"), task_id, kind="internal", state="idle")

    dashboard = build_task_dashboard(task, event_store, item_store, worker_store)
    lane = dashboard["workers"][0]
    assert lane["worker_id"] == worker.id
    assert lane["last_active_at"] is not None


def test_dashboard_resolves_manager_conversation_id(db_uri: str) -> None:
    """The card exposes the durable manager row's live session pointer."""
    task_store = SqlAlchemyTaskStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    manager_store = SqlAlchemyManagerStore(db_uri)
    manager_id = _uid("mgr_live")
    conversation_id = _uid("mgr_live_conv")
    manager_store.upsert(
        manager_id,
        owner_user_id=None,
        role_key="manager:default",
        description="Release manager",
        conversation_id=conversation_id,
    )
    task_id = _uid("task_mgr_conv")
    task_store.create(
        task_id,
        "Managed task",
        "managed goal",
        state="active",
        manager_id=manager_id,
    )
    task = task_store.get(task_id)
    assert task is not None

    dashboard = build_task_dashboard(
        task, event_store, item_store, worker_store, manager_store=manager_store
    )
    assert dashboard["task"]["manager_id"] == manager_id
    assert dashboard["task"]["manager_conversation_id"] == conversation_id

    # A dangling manager reference (row deleted) resolves to no session.
    manager_store.delete(manager_id)
    dangling = build_task_dashboard(
        task, event_store, item_store, worker_store, manager_store=manager_store
    )
    assert dangling["task"]["manager_conversation_id"] is None

    # Callers without a manager store keep the field present but unset.
    legacy = build_task_dashboard(task, event_store, item_store, worker_store)
    assert legacy["task"]["manager_conversation_id"] is None
