"""Tests for managed agent task routes (``/v1/agent-tasks``)."""

from __future__ import annotations

import uuid

import httpx
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import (
    TASK_MANAGER_AGENT_NAME,
    TASK_SECRETARY_ROLE,
    resolve_task_agent_id,
)
from omnigent.agent_tasks.secretary_session import NO_HOST_AVAILABLE_MESSAGE
from omnigent.db.utils import generate_agent_id
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.host_store import HostStore
from omnigent.entities import EventTag
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from tests.server.routes.agent_task_api import (
    agent_role_profile_url,
    agent_role_session_reset_url,
    agent_role_session_url,
    put_agent_role_profile,
)


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest_asyncio.fixture()
async def task_manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    """Return the seeded task-manager built-in agent id."""
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


@pytest_asyncio.fixture()
async def secretary_agent_id(db_uri: str) -> str:
    """Register a secretary agent for profile tests."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="secretary-agent", bundle_location="test:///bundle")
    return agent_id


def _create_payload(agent_profile_id: str, **overrides: object) -> dict:
    base: dict = {
        "agent_profile_id": agent_profile_id,
        "title": "S3 upload reliability",
        "internal_note": "retry flaky uploads",
        "tags": [{"tag_type": "domain", "tag": "s3"}],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


async def test_create_and_get_task(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
) -> None:
    """Creating a task returns the task snapshot; GET includes tags."""
    create_resp = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(task_manager_agent_id),
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["object"] == "agent.task"
    assert created["agent_profile_id"] == task_manager_agent_id
    assert created["state"] == "idle"
    assert created["tags"] == [{"tag_type": "domain", "tag": "s3"}]

    get_resp = await client.get(f"/v1/agent-tasks/{created['id']}")
    assert get_resp.status_code == 200
    loaded = get_resp.json()
    assert loaded["id"] == created["id"]
    assert loaded["title"] == "S3 upload reliability"
    assert loaded["tags"] == created["tags"]


async def test_create_rejects_missing_agent_profile(client: httpx.AsyncClient) -> None:
    """Unknown agent_profile_id returns 404."""
    resp = await client.post(
        "/v1/agent-tasks",
        json={
            "agent_profile_id": _uid("missing_profile"),
            "title": "Orphan task",
        },
    )
    assert resp.status_code == 404


async def test_list_tasks_filters_by_state(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
) -> None:
    """List endpoint filters by state query param."""
    idle_task = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(task_manager_agent_id, title="Idle task"),
    )
    archived = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(task_manager_agent_id, title="Archived task"),
    )
    await client.delete(f"/v1/agent-tasks/{archived.json()['id']}")

    list_resp = await client.get("/v1/agent-tasks?state=idle")
    assert list_resp.status_code == 200
    ids = {row["id"] for row in list_resp.json()["data"]}
    assert idle_task.json()["id"] in ids
    assert archived.json()["id"] not in ids


async def test_patch_task(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
) -> None:
    """PATCH updates mutable fields."""
    created = (
        await client.post("/v1/agent-tasks", json=_create_payload(task_manager_agent_id))
    ).json()
    patch_resp = await client.patch(
        f"/v1/agent-tasks/{created['id']}",
        json={"title": "Renamed task", "state": "pending"},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["title"] == "Renamed task"
    assert body["state"] == "pending"


async def test_put_tags_replaces_all(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
) -> None:
    """PUT /tags replaces the full tag set."""
    created = (
        await client.post("/v1/agent-tasks", json=_create_payload(task_manager_agent_id))
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
    task_manager_agent_id: str,
    db_uri: str,
) -> None:
    """Execution history is exposed for a task."""
    created = (
        await client.post("/v1/agent-tasks", json=_create_payload(task_manager_agent_id))
    ).json()
    task_id = created["id"]
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    manager_agent_id = _uid("mgr_exec")
    event_id = _uid("event_exec")
    task_item_id = _uid("item_exec")
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build passed",
        task_id=task_id,
        tags=[EventTag(tag_type="domain", tag="ci")],
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
        task_id=task_id,
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
    task_manager_agent_id: str,
) -> None:
    """DELETE soft-archives the task."""
    created = (
        await client.post("/v1/agent-tasks", json=_create_payload(task_manager_agent_id))
    ).json()
    delete_resp = await client.delete(f"/v1/agent-tasks/{created['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True
    assert delete_resp.json()["state"] == "archived"

    get_resp = await client.get(f"/v1/agent-tasks/{created['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["state"] == "archived"


async def test_unknown_task_agent_role_returns_404(client: httpx.AsyncClient) -> None:
    profile_resp = await client.get(agent_role_profile_url("manager"))
    assert profile_resp.status_code == 404


async def test_secretary_profile_and_bootstrap(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
    secretary_agent_id: str,
) -> None:
    """Secretary profile defaults feed manager bootstrap."""
    profile_resp = await put_agent_role_profile(
        client,
        role=TASK_SECRETARY_ROLE,
        agent_profile_id=secretary_agent_id,
        host_id=_uid("secretary_host"),
        workspace="/tmp/secretary",
    )
    assert profile_resp.status_code == 200
    assert profile_resp.json()["harness"] == "cursor"

    created = await client.post(
        "/v1/agent-tasks",
        json={"agent_profile_id": task_manager_agent_id, "title": "Bootstrap me"},
    )
    task_id = created.json()["id"]
    bootstrap_resp = await client.post(f"/v1/agent-tasks/{task_id}/bootstrap", json={})
    assert bootstrap_resp.status_code == 200
    assert bootstrap_resp.json()["manager_conversation_id"] is not None


async def _put_secretary_profile(client: httpx.AsyncClient, secretary_agent_id: str) -> None:
    profile_resp = await put_agent_role_profile(
        client,
        role=TASK_SECRETARY_ROLE,
        agent_profile_id=secretary_agent_id,
        host_id=_uid("secretary_host"),
        workspace="/tmp/secretary",
    )
    assert profile_resp.status_code == 200


async def test_ensure_secretary_session_seeds_prompt(
    client: httpx.AsyncClient,
    secretary_agent_id: str,
) -> None:
    await _put_secretary_profile(client, secretary_agent_id)

    ensure_resp = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_resp.status_code == 200
    body = ensure_resp.json()
    assert body["created"] is True
    conversation_id = body["conversation_id"]

    items_resp = await client.get(f"/v1/sessions/{conversation_id}/items")
    assert items_resp.status_code == 200
    items = items_resp.json()["data"]
    assert len(items) == 1
    assert items[0]["role"] == "user"
    assert items[0].get("is_meta") is True
    assert "docs/agent-tasks/README.md" in items_resp.text
    assert "docs/agent-tasks/TASK_SECRETARY.md" in items_resp.text
    assert "secretary" in items_resp.text.lower()

    profile_resp = await client.get(agent_role_profile_url(TASK_SECRETARY_ROLE))
    assert profile_resp.json()["conversation_id"] == conversation_id

    ensure_again = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_again.status_code == 200
    assert ensure_again.json()["created"] is False
    assert ensure_again.json()["conversation_id"] == conversation_id


async def test_reset_secretary_session_reseeds_prompt(
    client: httpx.AsyncClient,
    secretary_agent_id: str,
) -> None:
    await _put_secretary_profile(client, secretary_agent_id)
    first = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    first_id = first.json()["conversation_id"]

    reset_resp = await client.post(agent_role_session_reset_url(TASK_SECRETARY_ROLE))
    assert reset_resp.status_code == 200
    reset_body = reset_resp.json()
    assert reset_body["created"] is True
    assert reset_body["conversation_id"] != first_id

    deleted = await client.get(f"/v1/sessions/{first_id}")
    assert deleted.status_code == 404

    items_resp = await client.get(f"/v1/sessions/{reset_body['conversation_id']}/items")
    items = items_resp.json()["data"]
    assert len(items) == 1
    assert items[0].get("is_meta") is True
    assert "docs/agent-tasks/README.md" in items_resp.text
    assert "docs/agent-tasks/TASK_SECRETARY.md" in items_resp.text

    profile_resp = await client.get(agent_role_profile_url(TASK_SECRETARY_ROLE))
    assert profile_resp.json()["conversation_id"] == reset_body["conversation_id"]


async def test_ensure_secretary_session_auto_provisions_profile(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """First ensure creates the profile and session without a prior PUT."""
    host_id = _uid("auto_secretary_host")
    HostStore(db_uri).upsert_on_connect(host_id, "auto-secretary-host", RESERVED_USER_LOCAL)

    ensure_resp = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_resp.status_code == 200
    body = ensure_resp.json()
    assert body["created"] is True

    profile_resp = await client.get(agent_role_profile_url(TASK_SECRETARY_ROLE))
    assert profile_resp.status_code == 200
    profile = profile_resp.json()
    assert profile["host_id"] == host_id
    assert profile["conversation_id"] == body["conversation_id"]


async def test_ensure_secretary_session_fails_when_no_host_available(
    client: httpx.AsyncClient,
) -> None:
    """Auto-provision refuses to create a profile when no live host exists."""
    ensure_resp = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_resp.status_code == 400
    assert ensure_resp.json()["error"]["message"] == NO_HOST_AVAILABLE_MESSAGE
