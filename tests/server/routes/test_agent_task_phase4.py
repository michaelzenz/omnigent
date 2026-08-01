"""Phase 4 route tests: task items, dispatch, dashboard, completion."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_AGENT_NAME, resolve_task_agent_id
from omnigent.agent_tasks.completion import (
    TaskCompletionContext,
    configure_task_completion,
    notify_worker_session_status,
)
from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def task_manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


@pytest_asyncio.fixture()
async def worker_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-worker-agent", bundle_location="test:///bundle")
    return agent_id


def _bootstrap_body() -> dict[str, str]:
    return {
        "host_id": _uid("host_test"),
        "workspace": "/tmp/omnigent-task-test",
        "harness": "cursor",
        "model": "composer-2.5",
    }


def _item_payload(worker_agent_id: str) -> dict[str, str]:
    return {
        "title": "Investigate failure",
        "instructions": "Read logs and summarize the root cause.",
        "worker_agent_id": worker_agent_id,
        **_bootstrap_body(),
    }


async def _bootstrapped_task(client: httpx.AsyncClient, task_manager_agent_id: str) -> str:
    created = await client.post(
        "/v1/agent-tasks",
        json={"agent_profile_id": task_manager_agent_id, "title": "Phase 4 task"},
    )
    task_id = created.json()["id"]
    bootstrap = await client.post(
        f"/v1/agent-tasks/{task_id}/bootstrap",
        json=_bootstrap_body(),
    )
    assert bootstrap.status_code == 200
    return task_id


async def test_dispatch_and_dashboard(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
    worker_agent_id: str,
    db_uri: str,
) -> None:
    """Dispatch creates an execution visible on the task dashboard."""
    task_id = await _bootstrapped_task(client, task_manager_agent_id)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("routed_event")
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build failed",
        task_id=task_id,
        state="routed",
    )

    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(worker_agent_id),
            "state": "approved",
            "event_ids": [event_id],
        },
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]

    dispatch_resp = await client.post(
        f"/v1/task-items/{item_id}/dispatch",
        json=_bootstrap_body(),
    )
    assert dispatch_resp.status_code == 200
    body = dispatch_resp.json()
    assert body["status"] == "running"
    assert body["conversation_id"] is not None

    dashboard_resp = await client.get(f"/v1/agent-tasks/{task_id}/dashboard")
    assert dashboard_resp.status_code == 200
    dashboard = dashboard_resp.json()
    assert dashboard["derived"]["has_running_workers"] is True
    assert len(dashboard["workers"]) == 1
    assert dashboard["workers"][0]["worker_agent_id"] == worker_agent_id
    assert dashboard["workers"][0]["executions"][0]["task_item_id"] == item_id


async def test_item_accept_dispatches_worker(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
    worker_agent_id: str,
) -> None:
    """User can accept a task item and dispatch a worker."""
    task_id = await _bootstrapped_task(client, task_manager_agent_id)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(worker_agent_id),
            "submit_for_user_ack": True,
        },
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]
    assert item_resp.json()["state"] == "awaiting_user_ack"

    resolve_resp = await client.post(
        f"/v1/task-items/{item_id}/resolve",
        json={"resolution": "accept_item"},
    )
    assert resolve_resp.status_code == 200
    resolved = resolve_resp.json()
    assert resolved["state"] == "running"
    assert resolved["execution_id"] is not None
    assert resolved["worker_conversation_id"] is not None


async def test_item_edit_and_dispatch(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
    worker_agent_id: str,
) -> None:
    """User-edited item payload is used for dispatch."""
    task_id = await _bootstrapped_task(client, task_manager_agent_id)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(worker_agent_id),
            "submit_for_user_ack": True,
        },
    )
    item_id = item_resp.json()["id"]
    resolve_resp = await client.post(
        f"/v1/task-items/{item_id}/resolve",
        json={
            "resolution": "edit_and_dispatch",
            "edited_payload": {
                "instructions": "Apply the patch and run unit tests only.",
            },
        },
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["worker_conversation_id"] is not None


async def test_patch_queued_task_item(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
    worker_agent_id: str,
) -> None:
    """Queued work items can be edited before dispatch."""
    task_id = await _bootstrapped_task(client, task_manager_agent_id)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(worker_agent_id),
            "state": "queued",
        },
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]

    patch_resp = await client.patch(
        f"/v1/task-items/{item_id}",
        json={
            "title": "Updated title",
            "instructions": "Updated instructions",
        },
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["title"] == "Updated title"
    assert body["instructions"] == "Updated instructions"


async def test_worker_completion_hook(
    db_uri: str,
    task_manager_agent_id: str,
    worker_agent_id: str,
) -> None:
    """Worker idle status completes execution and wakes manager binding."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)

    task_id = _uid("task_complete")
    event_id = _uid("event_complete")
    task_item_id = _uid("item_complete")
    task_store.create(task_id, "Completion task", agent_profile_id=task_manager_agent_id)
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=task_manager_agent_id,
        host_id=_uid("host_mgr"),
        workspace="/tmp/mgr",
    )
    task_store.update(task_id, manager_conversation_id=manager_conv.id)
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Done",
        task_id=task_id,
        state="routed",
    )
    item_store.create_item(
        task_item_id,
        task_id,
        "Completion item",
        state="running",
        worker_agent_id=worker_agent_id,
    )
    worker_conv = conversation_store.create_conversation(
        kind="sub_agent",
        title="Worker",
        parent_conversation_id=manager_conv.id,
        agent_id=worker_agent_id,
        host_id=_uid("host_worker"),
        workspace="/tmp/worker",
    )
    execution = event_store.create_execution(
        _uid("exec_complete"),
        task_item_id,
        task_id,
        task_manager_agent_id,
        worker_agent_id,
        event_id=event_id,
        status="running",
        conversation_id=worker_conv.id,
    )
    event_store.upsert_binding(
        worker_conv.id,
        task_id,
        task_manager_agent_id,
        "worker",
        manager_conversation_id=manager_conv.id,
    )

    configure_task_completion(
        TaskCompletionContext(
            task_store=task_store,
            task_event_store=event_store,
            task_item_store=item_store,
            conversation_store=conversation_store,
            runner_router=None,
        )
    )
    handled = await notify_worker_session_status(
        worker_conv.id,
        "idle",
        output="Root cause was a stale credential.",
    )
    assert handled is True
    updated = event_store.get_execution(execution.id)
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.result_summary == "Root cause was a stale credential."
    assert updated.task_item_id == task_item_id
    completed_item = item_store.get_item(task_item_id)
    assert completed_item is not None
    assert completed_item.state == "done"
