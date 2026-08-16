"""Tests for task event ingress (``POST /v1/task-events``)."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_MANAGER_AGENT_NAME,
    resolve_task_agent_id,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from tests.server.routes.agent_task_api import put_agent_role_profile


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


async def _broker_profile(client: httpx.AsyncClient, manager_agent_id: str) -> None:
    await put_agent_role_profile(
        client,
        role=TASK_BROKER_ROLE,
        agent_profile_id=manager_agent_id,
        host_id=_uid("host_ingress"),
        workspace="/tmp/ingress-test",
    )


async def test_ingress_auto_routes_matching_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _broker_profile(client, manager_agent_id)
    created = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Upload retries",
            "goal": "all uploads retry to success",
            "tags": [{"tag_type": "repo", "tag": "omnigent-fork"}],
        },
    )
    task_id = created.json()["id"]

    ingress = await client.post(
        "/v1/task-events",
        json={
            "event_type": "build.finished",
            "title": "Upload retries failed",
            "summary": "repo omnigent-fork upload flaky",
            "source": "ci",
            "source_key": "build-upload-1",
            "tags": [{"tag_type": "repo", "tag": "omnigent-fork"}],
        },
    )
    assert ingress.status_code == 200
    body = ingress.json()
    assert body["state"] == "routed"
    assert ingress.json()["state"] == "routed"
    assert body["task_id"] == task_id


async def test_ingress_fast_paths_explicit_task_id(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _broker_profile(client, manager_agent_id)
    created = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Land PR #123",
            "goal": "PR #123 lands on main",
            "internal_note": "land pr 123 after blocker merges",
        },
    )
    task_id = created.json()["id"]

    ingress = await client.post(
        "/v1/task-events",
        json={
            "event_type": "github.pr.merged",
            "title": "Blocker PR merged",
            "summary": "repo:org/repo pr:456 merged unblocks:pr:123",
            "task_id": task_id,
            "source": "poll_plugin:github_pr",
            "source_key": "org/repo#456",
            "source_offset": 1,
        },
    )
    assert ingress.status_code == 200
    body = ingress.json()
    assert body["state"] == "routed"
    assert body["task_id"] == task_id


async def test_ingress_rejects_unknown_task_id(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/task-events",
        json={
            "event_type": "github.pr.merged",
            "title": "Blocker PR merged",
            "task_id": "missing-task-id",
            "source": "poll_plugin:github_pr",
            "source_key": "org/repo#456",
            "source_offset": 1,
        },
    )
    assert resp.status_code == 404


async def test_ingress_dedupes_by_source(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _broker_profile(client, manager_agent_id)
    payload = {
        "event_type": "build.finished",
        "title": "Dedup me",
        "source": "ci",
        "source_key": "dedup-key",
    }
    first = await client.post("/v1/task-events", json=payload)
    second = await client.post("/v1/task-events", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


async def test_ingress_rejects_session_internal_types(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/task-events",
        json={
            "event_type": "session.adoption",
            "title": "Nope",
        },
    )
    assert resp.status_code == 400


async def test_complete_requires_routed_state(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _broker_profile(client, manager_agent_id)
    await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Upload retries",
            "goal": "all uploads retry to success",
            "tags": [{"tag_type": "repo", "tag": "omnigent-fork"}],
        },
    )
    ingress = await client.post(
        "/v1/task-events",
        json={
            "event_type": "build.finished",
            "title": "Upload retries failed",
            "summary": "repo omnigent-fork upload flaky",
            "source": "ci",
            "source_key": "complete-key",
            "tags": [{"tag_type": "repo", "tag": "omnigent-fork"}],
        },
    )
    assert ingress.json()["state"] == "routed"
    event_id = ingress.json()["id"]
    complete = await client.post(f"/v1/task-events/{event_id}/complete")
    assert complete.status_code == 200
    assert complete.json()["state"] == "reconciled"
