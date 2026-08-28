"""Tests for task event ingress (``POST /v1/task-events``)."""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import TASK_BROKER_ROLE
from omnigent.db.utils import generate_agent_id
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.host_store import HostStore
from tests.server.routes.agent_task_api import patch_host_session_launch, put_agent_role_profile

# The roles redesign removed the shared constant/resolver; the manager agent
# name is only a lookup key for the fixture's self-registered agent.
_MANAGER_AGENT_NAME = "task-manager"


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture(autouse=True)
def _patch_host_session_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_host_session_launch(monkeypatch)


@pytest_asyncio.fixture()
async def manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    HostStore(db_uri).upsert_on_connect(
        _uid("host_ingress"),
        "host_ingress",
        RESERVED_USER_LOCAL,
    )
    agent_store = SqlAlchemyAgentStore(db_uri)
    existing = agent_store.get_by_name(_MANAGER_AGENT_NAME)
    if existing is not None:
        return existing.id
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name=_MANAGER_AGENT_NAME, bundle_location="test:///bundle")
    return agent_id


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
            "state": "active",
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
    assert ingress.status_code == 200, ingress.text
    body = ingress.json()
    assert body["state"] == "routed"
    assert ingress.json()["state"] == "routed"
    assert body["task_id"] == task_id


async def test_ingress_broadcasts_to_subscribers(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _broker_profile(client, manager_agent_id)
    task_ids: list[str] = []
    for title in ("Land PR #123", "Follow-up work"):
        created = await client.post(
            "/v1/agent-tasks",
            json={"title": title, "goal": f"{title} goal", "state": "active"},
        )
        task_ids.append(created.json()["id"])
    for task_id in task_ids:
        subscribed = await client.post(
            f"/v1/agent-tasks/{task_id}/event-subscriptions",
            json={"source": "poll_plugin:github_pr", "source_key": "org/repo#456"},
        )
        assert subscribed.status_code == 201, subscribed.text

    ingress = await client.post(
        "/v1/task-events",
        json={
            "event_type": "github.pr.merged",
            "title": "Blocker PR merged",
            "source": "poll_plugin:github_pr",
            "source_key": "org/repo#456",
            "source_offset": "1",
        },
    )
    assert ingress.status_code == 200, ingress.text
    body = ingress.json()
    assert body["state"] == "broadcast"
    assert body["task_id"] is None
    deliveries = body["deliveries"]
    assert {delivery["task_id"] for delivery in deliveries} == set(task_ids)

    # Dedup: re-posting the same source tuple returns the canonical event with
    # its recorded deliveries instead of fanning out again.
    replay = await client.post(
        "/v1/task-events",
        json={
            "event_type": "github.pr.merged",
            "title": "Blocker PR merged",
            "source": "poll_plugin:github_pr",
            "source_key": "org/repo#456",
            "source_offset": "1",
        },
    )
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["id"] == body["id"]
    assert {delivery["event_id"] for delivery in replay_body["deliveries"]} == {
        delivery["event_id"] for delivery in deliveries
    }


async def test_ingress_ignores_producer_task_id(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    await _broker_profile(client, manager_agent_id)
    ingress = await client.post(
        "/v1/task-events",
        json={
            "event_type": "github.pr.merged",
            "title": "Blocker PR merged",
            "task_id": "missing-task-id",
            "source": "poll_plugin:github_pr",
            "source_key": "org/repo#789",
            "source_offset": "1",
        },
    )
    assert ingress.status_code == 200, ingress.text
    body = ingress.json()
    assert body["task_id"] is None
    assert body["state"] == "awaiting_grouping"


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
            "state": "active",
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
