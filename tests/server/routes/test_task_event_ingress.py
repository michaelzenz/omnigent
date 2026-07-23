"""Tests for task event ingress (``POST /v1/task-events``)."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    return agent_id


async def _secretary_profile(client: httpx.AsyncClient, manager_agent_id: str) -> None:
    await client.put(
        "/v1/agent-tasks/secretary/profile",
        json={
            "agent_id": manager_agent_id,
            "host_id": _uid("host_ingress"),
            "workspace": "/tmp/ingress-test",
            "harness": "cursor",
            "model": "composer-2.5",
        },
    )


async def test_ingress_auto_routes_matching_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _secretary_profile(client, manager_agent_id)
    created = await client.post(
        "/v1/agent-tasks",
        json={
            "manager_agent_id": manager_agent_id,
            "title": "Upload retries",
            "charter": "flaky upload retries repo:omnigent-fork",
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
    assert body["state"] == "awaiting_manager_triage"
    assert ingress.json()["state"] == "awaiting_manager_triage"
    assert body["task_id"] == task_id


async def test_ingress_fast_paths_explicit_task_id(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _secretary_profile(client, manager_agent_id)
    created = await client.post(
        "/v1/agent-tasks",
        json={
            "manager_agent_id": manager_agent_id,
            "title": "Land PR #123",
            "charter": "land pr 123 after blocker merges",
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
    assert body["state"] == "awaiting_manager_triage"
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
    await _secretary_profile(client, manager_agent_id)
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


async def test_ingress_rejects_manager_internal_types(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/task-events",
        json={
            "event_type": "manager.proposal",
            "title": "Nope",
        },
    )
    assert resp.status_code == 400


async def test_complete_requires_manager_triage(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _secretary_profile(client, manager_agent_id)
    await client.post(
        "/v1/agent-tasks",
        json={
            "manager_agent_id": manager_agent_id,
            "title": "Upload retries",
            "charter": "flaky upload retries repo:omnigent-fork",
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
    assert ingress.json()["state"] == "awaiting_manager_triage"
    event_id = ingress.json()["id"]
    complete = await client.post(f"/v1/task-events/{event_id}/complete")
    assert complete.status_code == 200
    assert complete.json()["state"] == "processed"
