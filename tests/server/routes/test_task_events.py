"""Tests for managed task event routes (``/v1/task-events``)."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_AGENT_NAME, resolve_task_agent_id
from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


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
        state="awaiting_grouping",
    )
    create_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "agent_profile_id": manager_agent_id,
            "title": "Upload retries",
        },
    )
    task_id = create_resp.json()["id"]

    resolve_resp = await client.post(
        "/v1/task-events/batch-resolve",
        json={
            "event_ids": [event_id],
            "task_id": task_id,
            **_bootstrap_body(),
        },
    )
    assert resolve_resp.status_code == 200
    resolved = resolve_resp.json()["data"][0]
    assert resolved["state"] == "routed"
    assert resolved["task_id"] == task_id

    task_resp = await client.get(f"/v1/agent-tasks/{task_id}")
    assert task_resp.json()["manager_conversation_id"] is not None


async def test_dismiss_event(
    client: httpx.AsyncClient,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    event_id = _uid("event_dismiss")
    task_event_store.create_event(
        event_id=event_id,
        event_type="build.failed",
        title="Build failed",
        state="awaiting_grouping",
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
            "agent_profile_id": manager_agent_id,
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
