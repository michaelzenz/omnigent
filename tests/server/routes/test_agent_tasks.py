"""Tests for managed agent task routes (``/v1/agent-tasks``)."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.db.utils import generate_agent_id
from omnigent.entities import TaskEventTag
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def manager_agent_id(db_uri: str) -> str:
    """Register a manager agent for task CRUD tests."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    return agent_id


def _create_payload(manager_agent_id: str, **overrides: object) -> dict:
    base: dict = {
        "manager_agent_id": manager_agent_id,
        "title": "S3 upload reliability",
        "charter": "retry flaky uploads",
        "tags": [{"tag_type": "domain", "tag": "s3"}],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


async def test_create_and_get_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    """Creating a task returns the task snapshot; GET includes tags."""
    create_resp = await client.post("/v1/agent-tasks", json=_create_payload(manager_agent_id))
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["object"] == "agent.task"
    assert created["manager_agent_id"] == manager_agent_id
    assert created["state"] == "active"
    assert created["tags"] == [{"tag_type": "domain", "tag": "s3"}]

    get_resp = await client.get(f"/v1/agent-tasks/{created['id']}")
    assert get_resp.status_code == 200
    loaded = get_resp.json()
    assert loaded["id"] == created["id"]
    assert loaded["title"] == "S3 upload reliability"
    assert loaded["tags"] == created["tags"]


async def test_create_rejects_missing_manager_agent(client: httpx.AsyncClient) -> None:
    """Unknown manager_agent_id returns 404."""
    resp = await client.post(
        "/v1/agent-tasks",
        json={
            "manager_agent_id": _uid("missing_mgr"),
            "title": "Orphan task",
        },
    )
    assert resp.status_code == 404


async def test_list_tasks_filters_by_state(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    """List endpoint filters by state query param."""
    active = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(manager_agent_id, title="Active task"),
    )
    archived = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(manager_agent_id, title="Archived task"),
    )
    await client.delete(f"/v1/agent-tasks/{archived.json()['id']}")

    list_resp = await client.get("/v1/agent-tasks?state=active")
    assert list_resp.status_code == 200
    ids = {row["id"] for row in list_resp.json()["data"]}
    assert active.json()["id"] in ids
    assert archived.json()["id"] not in ids


async def test_search_tasks(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    """Text search finds tasks by charter/title."""
    await client.post(
        "/v1/agent-tasks",
        json=_create_payload(manager_agent_id, charter="unique-flaky-upload-token"),
    )
    resp = await client.get("/v1/agent-tasks?q=unique-flaky-upload-token")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


async def test_patch_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    """PATCH updates mutable fields."""
    created = (
        await client.post("/v1/agent-tasks", json=_create_payload(manager_agent_id))
    ).json()
    patch_resp = await client.patch(
        f"/v1/agent-tasks/{created['id']}",
        json={"title": "Renamed task", "state": "paused"},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["title"] == "Renamed task"
    assert body["state"] == "paused"


async def test_put_tags_replaces_all(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    """PUT /tags replaces the full tag set."""
    created = (
        await client.post("/v1/agent-tasks", json=_create_payload(manager_agent_id))
    ).json()
    put_resp = await client.put(
        f"/v1/agent-tasks/{created['id']}/tags",
        json={
            "tags": [
                {"tag_type": "component", "tag": "build"},
                {"tag_type": "domain", "tag": "ci"},
            ]
        },
    )
    assert put_resp.status_code == 200
    tags = put_resp.json()["tags"]
    assert sorted(tags, key=lambda row: row["tag"]) == [
        {"tag_type": "component", "tag": "build"},
        {"tag_type": "domain", "tag": "ci"},
    ]


async def test_list_executions(
    client: httpx.AsyncClient,
    manager_agent_id: str,
    db_uri: str,
) -> None:
    """Execution history is exposed for a task."""
    created = (
        await client.post("/v1/agent-tasks", json=_create_payload(manager_agent_id))
    ).json()
    task_id = created["id"]
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_id = _uid("event_exec")
    task_item_id = _uid("item_exec")
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build passed",
        task_id=task_id,
        manager_agent_id=manager_agent_id,
        tags=[TaskEventTag(event_id=event_id, tag_type="domain", tag="ci")],
    )
    item_store.create_item(
        task_item_id,
        task_id,
        "Investigate build",
        state="running",
    )
    execution_id = _uid("execution_1")
    event_store.create_execution(
        execution_id=execution_id,
        task_item_id=task_item_id,
        event_id=event_id,
        task_id=task_id,
        manager_agent_id=manager_agent_id,
        worker_agent_id=manager_agent_id,
        status="succeeded",
    )
    event_store.update_execution(execution_id, status="succeeded", result_summary="done")

    resp = await client.get(f"/v1/agent-tasks/{task_id}/executions")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == execution_id
    assert rows[0]["status"] == "succeeded"


async def test_delete_archives_task(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    """DELETE soft-archives the task."""
    created = (
        await client.post("/v1/agent-tasks", json=_create_payload(manager_agent_id))
    ).json()
    delete_resp = await client.delete(f"/v1/agent-tasks/{created['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True
    assert delete_resp.json()["state"] == "archived"

    get_resp = await client.get(f"/v1/agent-tasks/{created['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["state"] == "archived"


async def test_secretary_profile_and_bootstrap(
    client: httpx.AsyncClient,
    manager_agent_id: str,
) -> None:
    """Secretary profile defaults feed manager bootstrap."""
    profile_resp = await client.put(
        "/v1/agent-tasks/secretary/profile",
        json={
            "agent_id": manager_agent_id,
            "host_id": _uid("secretary_host"),
            "workspace": "/tmp/secretary",
            "harness": "cursor",
            "model": "composer-2.5",
        },
    )
    assert profile_resp.status_code == 200
    assert profile_resp.json()["harness"] == "cursor"

    created = await client.post(
        "/v1/agent-tasks",
        json={"manager_agent_id": manager_agent_id, "title": "Bootstrap me"},
    )
    task_id = created.json()["id"]
    bootstrap_resp = await client.post(f"/v1/agent-tasks/{task_id}/bootstrap", json={})
    assert bootstrap_resp.status_code == 200
    assert bootstrap_resp.json()["manager_conversation_id"] is not None
