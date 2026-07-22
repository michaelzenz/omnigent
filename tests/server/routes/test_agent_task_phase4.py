"""Phase 4 route tests: dispatch, proposals, dashboard, completion."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.agent_tasks.completion import (
    TaskCompletionContext,
    configure_task_completion,
    notify_worker_session_status,
)
from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    return agent_id


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


def _dispatch_payload(worker_agent_id: str) -> dict[str, str]:
    return {
        "worker_agent_id": worker_agent_id,
        "title": "Investigate failure",
        "instructions": "Read logs and summarize the root cause.",
        **_bootstrap_body(),
    }


async def _bootstrapped_task(client: httpx.AsyncClient, manager_agent_id: str) -> str:
    created = await client.post(
        "/v1/agent-tasks",
        json={"manager_agent_id": manager_agent_id, "title": "Phase 4 task"},
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
    manager_agent_id: str,
    worker_agent_id: str,
    db_uri: str,
) -> None:
    """Dispatch creates an execution visible on the task dashboard."""
    task_id = await _bootstrapped_task(client, manager_agent_id)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("routed_event")
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build failed",
        task_id=task_id,
        state="routed",
    )

    dispatch_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/dispatch",
        json={"event_id": event_id, **_dispatch_payload(worker_agent_id)},
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


async def test_proposal_accept_dispatches_worker(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    worker_agent_id: str,
) -> None:
    """User can accept a manager proposal and dispatch a worker."""
    task_id = await _bootstrapped_task(client, manager_agent_id)
    proposal_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/events",
        json={
            "event_type": "manager.proposal",
            "title": "Retry with backoff",
            "payload": _dispatch_payload(worker_agent_id),
        },
    )
    assert proposal_resp.status_code == 200
    proposal_id = proposal_resp.json()["id"]

    resolve_resp = await client.post(
        f"/v1/task-events/{proposal_id}/resolve",
        json={"resolution": "accept_proposal"},
    )
    assert resolve_resp.status_code == 200
    resolved = resolve_resp.json()
    assert resolved["state"] == "processed"
    assert resolved["execution_id"] is not None


async def test_proposal_edit_and_dispatch(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    worker_agent_id: str,
) -> None:
    """User-edited proposal payload is used for dispatch."""
    task_id = await _bootstrapped_task(client, manager_agent_id)
    proposal_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/events",
        json={
            "event_type": "manager.proposal",
            "title": "Patch retry logic",
            "payload": _dispatch_payload(worker_agent_id),
        },
    )
    proposal_id = proposal_resp.json()["id"]
    resolve_resp = await client.post(
        f"/v1/task-events/{proposal_id}/resolve",
        json={
            "resolution": "edit_and_dispatch",
            "edited_payload": {
                "instructions": "Apply the patch and run unit tests only.",
            },
        },
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["worker_conversation_id"] is not None


async def test_worker_completion_hook(
    db_uri: str,
    manager_agent_id: str,
    worker_agent_id: str,
) -> None:
    """Worker idle status completes execution and wakes manager binding."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)

    task_id = _uid("task_complete")
    event_id = _uid("event_complete")
    task_store.create(task_id, manager_agent_id, "Completion task")
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_agent_id,
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
        event_id,
        task_id,
        manager_agent_id,
        worker_agent_id,
        status="running",
        conversation_id=worker_conv.id,
    )
    event_store.upsert_binding(
        worker_conv.id,
        task_id,
        manager_agent_id,
        "worker",
        manager_conversation_id=manager_conv.id,
    )

    configure_task_completion(
        TaskCompletionContext(
            task_store=task_store,
            task_event_store=event_store,
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
