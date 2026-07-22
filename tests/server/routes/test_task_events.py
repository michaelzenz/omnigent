"""Tests for managed task event routes (``/v1/task-events``)."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    return agent_id


@pytest_asyncio.fixture()
def task_event_store(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


def _bootstrap_body() -> dict[str, str]:
    return {
        "host_id": _uid("host_test"),
        "workspace": "/tmp/omnigent-task-test",
        "harness": "cursor",
        "model": "composer-2.5",
    }


async def test_resolve_routes_event_and_bootstraps_manager(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    event_id = _uid("event_resolve")
    task_event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build finished",
        state="awaiting_new_manager_decision",
    )
    create_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "manager_agent_id": manager_agent_id,
            "title": "Upload retries",
        },
    )
    task_id = create_resp.json()["id"]

    resolve_resp = await client.post(
        f"/v1/task-events/{event_id}/resolve",
        json={
            "resolution": "route_to_task",
            "task_id": task_id,
            **_bootstrap_body(),
        },
    )
    assert resolve_resp.status_code == 200
    resolved = resolve_resp.json()
    assert resolved["state"] == "awaiting_manager_triage"
    assert resolved["task_id"] == task_id
    assert resolved["manager_conversation_id"] is not None

    task_resp = await client.get(f"/v1/agent-tasks/{task_id}")
    assert task_resp.json()["manager_conversation_id"] == resolved["manager_conversation_id"]


async def test_select_attempt_resolution(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    create_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "manager_agent_id": manager_agent_id,
            "title": "Notes task",
        },
    )
    created_task_id = create_resp.json()["id"]
    event_id = _uid("event_select")
    attempt_id = _uid("attempt_select")
    task_event_store.create_event(
        event_id=event_id,
        event_type="note.added",
        title="New note",
        state="awaiting_user_selection",
    )
    task_event_store.create_routing_attempt(
        attempt_id=attempt_id,
        event_id=event_id,
        candidate_task_id=created_task_id,
        candidate_manager_agent_id=manager_agent_id,
        rank=1,
        decision="accepted",
    )

    resolve_resp = await client.post(
        f"/v1/task-events/{event_id}/resolve",
        json={
            "resolution": "select_attempt",
            "routing_attempt_id": attempt_id,
            **_bootstrap_body(),
        },
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["selected_routing_attempt_id"] == attempt_id


async def test_dismiss_event(
    client: httpx.AsyncClient,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    event_id = _uid("event_dismiss")
    task_event_store.create_event(
        event_id=event_id,
        event_type="build.failed",
        title="Build failed",
        state="awaiting_new_manager_decision",
    )
    resp = await client.post(f"/v1/task-events/{event_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["state"] == "dismissed"


async def test_bootstrap_rejects_dead_manager_session(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    dead_conversation_id = _uid("dead_conv")
    create_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "manager_agent_id": manager_agent_id,
            "title": "Dead session task",
            "manager_conversation_id": dead_conversation_id,
        },
    )
    task_id = create_resp.json()["id"]
    bootstrap_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/bootstrap",
        json=_bootstrap_body(),
    )
    assert bootstrap_resp.status_code == 409
