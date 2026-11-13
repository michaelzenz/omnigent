"""Tests for the GlobalHub manager routes (``/v1/agent-tasks/managers``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.agent_tasks.session_labels import MANAGER_ROLE_VALUE, ROLE_LABEL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE = {"X-Forwarded-Email": "alice@example.com"}
BOB = {"X-Forwarded-Email": "bob@example.com"}
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
ROUTE = "/v1/agent-tasks/managers"


def _install_manager_agent(db_uri: str) -> None:
    SqlAlchemyAgentStore(db_uri).create(uuid.uuid4().hex, "onih-pmv2", "bundles/onih-pmv2.tar.gz")


def _place_globalhub(db_uri: str) -> None:
    UserSettingsStore(db_uri).save_globalhub_placement(
        GlobalHubPlacement(host_id=HOST_ID, workspace="/Users/alice/globalhub"),
        user_id="alice@example.com",
    )


async def _create_manager(client: httpx.AsyncClient, title: str, **fields: Any) -> dict[str, Any]:
    response = await client.post(
        ROUTE, json={"title": title, "description": f"{title} work", **fields}, headers=ALICE
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create_task(client: httpx.AsyncClient, manager_id: str, title: str) -> str:
    response = await client.post(
        "/v1/agent-tasks",
        json={"manager_id": manager_id, "title": title, "goal": f"Finish {title}"},
        headers=ALICE,
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def test_create_needs_a_placement_and_starts_a_labeled_session(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _install_manager_agent(db_uri)
    unplaced = await auth_client.post(ROUTE, json={"description": "Storage"}, headers=ALICE)
    _place_globalhub(db_uri)

    manager = await _create_manager(auth_client, "Storage", role_key="manager:default")
    untitled = await auth_client.post(ROUTE, json={"description": "Misc"}, headers=ALICE)
    blank = await auth_client.post(ROUTE, json={"description": "  "}, headers=ALICE)
    templated = await auth_client.post(
        ROUTE, json={"description": "x", "role_key": "manager:infra"}, headers=ALICE
    )

    assert unplaced.status_code == 400
    assert (manager["title"], manager["description"], manager["tasks"]) == (
        "Storage",
        "Storage work",
        [],
    )
    session = SqlAlchemyConversationStore(db_uri).get_conversation(manager["conversation_id"])
    assert session is not None
    assert (session.labels[ROLE_LABEL], session.title, session.host_id) == (
        MANAGER_ROLE_VALUE,
        "Storage",
        HOST_ID,
    )
    assert untitled.json()["title"] == "Task manager"
    assert (blank.status_code, templated.status_code) == (422, 422)


async def test_list_shows_live_portfolios_and_only_the_callers_managers(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _install_manager_agent(db_uri)
    _place_globalhub(db_uri)
    storage = (await _create_manager(auth_client, "Storage"))["id"]
    await _create_manager(auth_client, "Dashboards")
    live = await _create_task(auth_client, storage, "live")
    archived = await _create_task(auth_client, storage, "archived")
    await auth_client.delete(f"/v1/agent-tasks/{archived}", headers=ALICE)

    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]

    portfolios = {
        manager["title"]: (manager["task_count"], manager["tasks"]) for manager in listed
    }
    assert portfolios == {
        "Storage": (1, [{"id": live, "title": "live", "state": "active"}]),
        "Dashboards": (0, []),
    }
    assert (await auth_client.get(ROUTE, headers=BOB)).json()["data"] == []


async def test_delete_unassigns_tasks_and_keeps_the_session(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _install_manager_agent(db_uri)
    _place_globalhub(db_uri)
    manager = await _create_manager(auth_client, "Storage")
    task_id = await _create_task(auth_client, manager["id"], "task")

    by_bob = await auth_client.delete(f"{ROUTE}/{manager['id']}", headers=BOB)
    deleted = await auth_client.delete(f"{ROUTE}/{manager['id']}", headers=ALICE)

    assert by_bob.status_code == 404
    assert deleted.json() == {
        "object": "agent.manager",
        "id": manager["id"],
        "deleted": True,
        "conversation_id": manager["conversation_id"],
    }
    task = (await auth_client.get(f"/v1/agent-tasks/{task_id}", headers=ALICE)).json()
    assert task["manager_id"] is None
    conversations = SqlAlchemyConversationStore(db_uri)
    assert conversations.get_conversation(manager["conversation_id"]) is not None
    assert (await auth_client.get(ROUTE, headers=ALICE)).json()["data"] == []
