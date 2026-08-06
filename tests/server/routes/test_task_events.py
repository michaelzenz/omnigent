"""Tests for managed task event routes (``/v1/task-events``)."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_MANAGER_AGENT_NAME,
    resolve_task_agent_id,
)
from omnigent.entities import EventTag, TaskTag
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from tests.server.routes.agent_task_api import put_agent_role_profile


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_profile_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


@pytest_asyncio.fixture()
def task_event_store(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


async def _put_broker_profile(
    client: httpx.AsyncClient,
    manager_agent_profile_id: str,
) -> None:
    profile_resp = await put_agent_role_profile(
        client,
        role=TASK_BROKER_ROLE,
        agent_profile_id=manager_agent_profile_id,
        host_id=_uid("host_test"),
        workspace="/tmp/omnigent-task-test",
    )
    assert profile_resp.status_code == 200


async def test_resolve_routes_event_and_bootstraps_manager(
    client: httpx.AsyncClient,
    manager_agent_profile_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    await _put_broker_profile(client, manager_agent_profile_id)
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
            "agent_profile_id": manager_agent_profile_id,
            "title": "Upload retries",
        },
    )
    task_id = create_resp.json()["id"]

    resolve_resp = await client.post(
        "/v1/task-events/batch-resolve",
        json={
            "event_ids": [event_id],
            "task_id": task_id,
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
    manager_agent_profile_id: str,
) -> None:
    await _put_broker_profile(client, manager_agent_profile_id)
    dead_conversation_id = _uid("dead_conv")
    create_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "agent_profile_id": manager_agent_profile_id,
            "title": "Dead session task",
            "manager_conversation_id": dead_conversation_id,
        },
    )
    task_id = create_resp.json()["id"]
    bootstrap_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/bootstrap",
        json={},
    )
    assert bootstrap_resp.status_code == 409


async def test_ambiguous_inbox_clusters_stalled_events(
    client: httpx.AsyncClient,
    manager_agent_profile_id: str,
    db_uri: str,
) -> None:
    """GET ambiguous-inbox groups stalled events and suggests task candidates."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    paused_id = _uid("ambiguous-paused")
    task_store.create(
        paused_id,
        "Upload retries",
        agent_profile_id=manager_agent_profile_id,
        state="pending",
        tags=[TaskTag(task_id=paused_id, tag_type="repo", tag="omnigent-fork")],
    )
    event_id = _uid("ambiguous-event")
    event_store.create_event(
        event_id,
        "build.finished",
        "Upload retries failed",
        state="awaiting_grouping",
        tags=[EventTag(tag_type="repo", tag="omnigent-fork")],
    )

    resp = await client.get("/v1/task-events/ambiguous-inbox")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "agent.task.ambiguous_inbox"
    cluster = body["clusters"][0]
    assert any(event["id"] == event_id for event in cluster["events"])
    assert cluster["suggested_candidates"][0]["task_id"] == paused_id


async def test_match_tasks_ranks_pending_tasks(
    client: httpx.AsyncClient,
    manager_agent_profile_id: str,
    db_uri: str,
) -> None:
    """POST match-tasks returns ranked active and pending task candidates."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    paused_id = _uid("match-paused-task")
    task_store.create(
        paused_id,
        "omnigent-fork",
        agent_profile_id=manager_agent_profile_id,
        state="pending",
        tags=[TaskTag(task_id=paused_id, tag_type="repo", tag="omnigent-fork")],
    )
    event_id = _uid("match-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        tags=[EventTag(tag_type="repo", tag="omnigent-fork")],
    )

    matched = await client.post(
        "/v1/task-events/match-tasks",
        json={"event_ids": [event_id]},
    )
    assert matched.status_code == 200
    candidates = matched.json()["candidates"]
    assert candidates[0]["task_id"] == paused_id
    assert candidates[0]["state"] == "pending"
