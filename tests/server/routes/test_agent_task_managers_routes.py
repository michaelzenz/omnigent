"""Tests for the GlobalHub manager routes (``/v1/agent-tasks/managers``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from omnigent.agent_tasks.session_labels import MANAGER_ROLE_VALUE, ROLE_LABEL
from omnigent.entities import MANAGER_TASK_CAPACITY, MAX_MANAGER_DESCRIPTION_CHARS
from omnigent.runner.identity import (
    ORCHESTRATION_SOURCE_HEADER,
    RUNNER_TUNNEL_TOKEN_HEADER,
    token_bound_runner_id,
)
from omnigent.server.routes import agent_task_managers
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE_ID = "alice@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": "bob@example.com"}
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
PINNED_HOST_ID = "1e2d3c4b5a69788796a5b4c3d2e1f00f"
ROUTE = "/v1/agent-tasks/managers"


def _install_manager_agent(db_uri: str) -> None:
    SqlAlchemyAgentStore(db_uri).create(uuid.uuid4().hex, "onih-pmv2", "bundles/onih-pmv2.tar.gz")


def _place_globalhub(db_uri: str, host_id: str = HOST_ID) -> None:
    UserSettingsStore(db_uri).save_globalhub_placement(
        GlobalHubPlacement(host_id=host_id, workspace="/Users/alice/globalhub"),
        user_id=ALICE_ID,
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
        json={"manager_id": manager_id, "title": title},
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


async def test_pinned_managers_run_in_the_hosts_home_and_list_their_session_placement(
    auth_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_manager_agent(db_uri)
    _place_globalhub(db_uri)
    offline = await auth_client.post(
        ROUTE, json={"description": "x", "host_id": PINNED_HOST_ID}, headers=ALICE
    )
    resolutions: list[dict[str, object]] = []

    async def home_directory(**kwargs: object) -> str:
        resolutions.append(kwargs)
        return "/home/alice"

    monkeypatch.setattr(agent_task_managers, "resolve_host_home_workspace", home_directory)
    pinned = await _create_manager(auth_client, "Storage", host_id=PINNED_HOST_ID)
    on_globalhub_host = await _create_manager(auth_client, "Dashboards", host_id=HOST_ID)
    _place_globalhub(db_uri, host_id="2d3c4b5a69788796a5b4c3d2e1f00f1e")
    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]

    assert (offline.status_code, "offline" in offline.text) == (400, True)
    assert [(call["user_id"], call["host_id"]) for call in resolutions] == [
        (ALICE_ID, PINNED_HOST_ID)
    ]
    assert (pinned["host_id"], pinned["workspace"]) == (PINNED_HOST_ID, "/home/alice")
    # Existing sessions stay where they were created when GlobalHub moves.
    assert {
        manager["title"]: (manager["host_id"], manager["workspace"]) for manager in listed
    } == {
        "Storage": (PINNED_HOST_ID, "/home/alice"),
        "Dashboards": (HOST_ID, "/Users/alice/globalhub"),
    }
    managers = SqlAlchemyManagerStore(db_uri)
    stored_pins = [
        managers.get(manager["id"], owner_user_id=ALICE_ID)
        for manager in (pinned, on_globalhub_host)
    ]
    assert [(row.host_id, row.workspace) for row in stored_pins if row is not None] == [
        (PINNED_HOST_ID, "/home/alice"),
        (None, None),
    ]


async def test_only_the_managers_own_session_updates_its_description(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _install_manager_agent(db_uri)
    _place_globalhub(db_uri)
    manager = await _create_manager(auth_client, "Storage")
    runner_token = "storage-manager-runner"
    SqlAlchemyConversationStore(db_uri).set_runner_id(
        manager["conversation_id"], token_bound_runner_id(runner_token)
    )
    own_runner = {
        ORCHESTRATION_SOURCE_HEADER: manager["conversation_id"],
        RUNNER_TUNNEL_TOKEN_HEADER: runner_token,
    }
    body = {"description": "Storage service and its S3 clients"}

    refused = [
        await auth_client.patch(f"{ROUTE}/self", json=body, headers=ALICE),
        await auth_client.patch(
            f"{ROUTE}/self",
            json=body,
            headers={**ALICE, **own_runner, RUNNER_TUNNEL_TOKEN_HEADER: "another-runner"},
        ),
        await auth_client.patch(f"{ROUTE}/self", json=body, headers={**BOB, **own_runner}),
    ]
    blank = await auth_client.patch(
        f"{ROUTE}/self", json={"description": " "}, headers={**ALICE, **own_runner}
    )
    longest, too_long = [
        await auth_client.patch(
            f"{ROUTE}/self", json={"description": "x" * length}, headers={**ALICE, **own_runner}
        )
        for length in (MAX_MANAGER_DESCRIPTION_CHARS, MAX_MANAGER_DESCRIPTION_CHARS + 1)
    ]
    updated = await auth_client.patch(f"{ROUTE}/self", json=body, headers={**ALICE, **own_runner})
    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]

    assert [response.status_code for response in refused] == [403, 403, 403]
    assert blank.status_code == 422
    assert (longest.status_code, too_long.status_code) == (200, 422)
    assert (updated.json()["id"], updated.json()["description"]) == (
        manager["id"],
        body["description"],
    )
    assert [entry["description"] for entry in listed] == [body["description"]]


async def test_list_shows_portfolios_missing_sessions_and_only_the_callers_managers(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _install_manager_agent(db_uri)
    _place_globalhub(db_uri)
    storage = (await _create_manager(auth_client, "Storage"))["id"]
    dashboards = await _create_manager(auth_client, "Dashboards")
    live = await _create_task(auth_client, storage, "live")
    archived = await _create_task(auth_client, storage, "archived")
    await auth_client.patch(
        f"/v1/agent-tasks/{archived}", json={"state": "archived"}, headers=ALICE
    )
    SqlAlchemyConversationStore(db_uri).update_conversation(
        dashboards["conversation_id"], archived=True
    )

    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]

    portfolios = {
        manager["title"]: (manager["task_count"], manager["tasks"], manager["session_missing"])
        for manager in listed
    }
    assert portfolios == {
        "Storage": (1, [{"id": live, "title": "live", "state": "active"}], False),
        "Dashboards": (0, [], True),
    }
    assert (await auth_client.get(ROUTE, headers=BOB)).json()["data"] == []


async def test_the_list_leaves_out_full_managers_unless_the_caller_asks(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _install_manager_agent(db_uri)
    _place_globalhub(db_uri)
    full = (await _create_manager(auth_client, "Storage"))["id"]
    with_room = (await _create_manager(auth_client, "Dashboards"))["id"]
    tasks = SqlAlchemyTaskStore(db_uri)
    # Every live state counts; an archived task does not.
    live_states = ("active", "idle", "agent-resolved")
    for manager_id, count in (
        (full, MANAGER_TASK_CAPACITY),
        (with_room, MANAGER_TASK_CAPACITY - 1),
    ):
        for index in range(count):
            tasks.create(
                uuid.uuid4().hex,
                owner_user_id=ALICE_ID,
                title=f"task {index}",
                manager_id=manager_id,
                state=live_states[index % len(live_states)],
            )
    tasks.create(
        uuid.uuid4().hex,
        owner_user_id=ALICE_ID,
        title="archived",
        manager_id=with_room,
        state="archived",
    )

    listed = (await auth_client.get(ROUTE, headers=ALICE)).json()["data"]
    everyone = (
        await auth_client.get(ROUTE, params={"include_full": "true"}, headers=ALICE)
    ).json()["data"]

    assert [manager["id"] for manager in listed] == [with_room]
    # Both managers share a creation second, so the test does not rely on their order.
    assert {manager["id"]: manager["task_count"] for manager in everyone} == {
        full: MANAGER_TASK_CAPACITY,
        with_room: MANAGER_TASK_CAPACITY - 1,
    }


async def test_the_list_comes_in_pages_that_skip_full_managers(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    managers = SqlAlchemyManagerStore(db_uri)
    ids = [
        managers.create(uuid.uuid4().hex, owner_user_id=ALICE_ID, title=f"manager {index}").id
        for index in range(4)
    ]
    order = [manager.id for manager in managers.list(owner_user_id=ALICE_ID)]
    tasks = SqlAlchemyTaskStore(db_uri)
    for index in range(MANAGER_TASK_CAPACITY):
        tasks.create(
            uuid.uuid4().hex,
            owner_user_id=ALICE_ID,
            title=f"task {index}",
            manager_id=order[1],
        )

    async def page(**params: object) -> dict[str, Any]:
        response = await auth_client.get(ROUTE, params=params, headers=ALICE)
        assert response.status_code == 200, response.text
        return response.json()

    first = await page(limit=2)
    second = await page(limit=2, after=first["last_id"])
    every = await page(include_full="true", limit=agent_task_managers.MAX_MANAGER_PAGE_SIZE)

    assert sorted(ids) == sorted(order)
    assert [entry["id"] for entry in first["data"]] == [order[0], order[2]]
    assert (first["first_id"], first["last_id"], first["has_more"]) == (order[0], order[2], True)
    assert [entry["id"] for entry in second["data"]] == [order[3]]
    assert second["has_more"] is False
    assert [entry["id"] for entry in every["data"]] == order
    assert len((await page())["data"]) == 3, "the default page holds every manager here"
    unknown = await auth_client.get(ROUTE, params={"after": "gone"}, headers=ALICE)
    assert unknown.status_code == 400
    for limit in (0, agent_task_managers.MAX_MANAGER_PAGE_SIZE + 1):
        too = await auth_client.get(ROUTE, params={"limit": limit}, headers=ALICE)
        assert too.status_code == 422


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
