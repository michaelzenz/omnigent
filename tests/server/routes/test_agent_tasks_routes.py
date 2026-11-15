"""Tests for the GlobalHub task routes (``/v1/agent-tasks``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore

ALICE = {"X-Forwarded-Email": "alice@example.com"}
BOB = {"X-Forwarded-Email": "bob@example.com"}
ROUTE = "/v1/agent-tasks"


def _manager(db_uri: str, owner: str = "alice@example.com") -> tuple[str, str]:
    manager_id, session_id = uuid.uuid4().hex, uuid.uuid4().hex
    SqlAlchemyManagerStore(db_uri).create(
        manager_id, owner_user_id=owner, title="Storage", conversation_id=session_id
    )
    return manager_id, session_id


async def _create(
    client: httpx.AsyncClient,
    manager_id: str,
    title: str,
    headers: dict[str, str] = ALICE,
    **fields: Any,
) -> str:
    response = await client.post(
        ROUTE,
        json={"manager_id": manager_id, "title": title, "goal": f"Finish {title}", **fields},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _board(
    client: httpx.AsyncClient, headers: dict[str, str] = ALICE, **params: Any
) -> list[str]:
    response = await client.get(ROUTE, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return [task["title"] for task in response.json()["data"]]


async def test_tasks_need_an_owned_manager_and_list_in_board_order(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id, _ = _manager(db_uri)
    bobs_manager, _ = _manager(db_uri, owner="bob@example.com")
    await _create(auth_client, manager_id, "first", state="active")
    await _create(auth_client, manager_id, "second")

    with_bobs_manager = await auth_client.post(
        ROUTE, json={"manager_id": bobs_manager, "title": "x", "goal": "y"}, headers=ALICE
    )
    born_idle = await auth_client.post(
        ROUTE,
        json={"manager_id": manager_id, "title": "x", "goal": "y", "state": "idle"},
        headers=ALICE,
    )
    blank_title = await auth_client.post(
        ROUTE, json={"manager_id": manager_id, "title": "  ", "goal": "y"}, headers=ALICE
    )

    assert await _board(auth_client) == ["second", "first"]
    assert await _board(auth_client, BOB) == []
    assert with_bobs_manager.status_code == 404
    assert born_idle.status_code == 422
    assert blank_title.status_code == 422


async def test_list_filters_by_state_manager_session_and_limit(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id, session_id = _manager(db_uri)
    other_manager, _ = _manager(db_uri)
    idle_task = await _create(auth_client, manager_id, "idle")
    await _create(auth_client, manager_id, "active")
    await _create(auth_client, other_manager, "elsewhere")
    await auth_client.patch(f"{ROUTE}/{idle_task}", json={"state": "idle"}, headers=ALICE)

    assert await _board(auth_client, state="idle") == ["idle"]
    assert await _board(auth_client, manager_conversation_id=session_id) == ["idle", "active"]
    assert await _board(auth_client, manager_conversation_id=uuid.uuid4().hex) == []
    assert await _board(auth_client, manager_id="not-an-id") == []
    assert len(await _board(auth_client, limit=1)) == 1


async def test_state_changes_move_the_card_and_updates_stay_owner_scoped(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id, _ = _manager(db_uri)
    bobs_manager, _ = _manager(db_uri, owner="bob@example.com")
    one = await _create(auth_client, manager_id, "one")
    await _create(auth_client, manager_id, "two")
    three = await _create(auth_client, manager_id, "three")

    await auth_client.patch(f"{ROUTE}/{three}", json={"state": "agent-resolved"}, headers=ALICE)
    assert await _board(auth_client) == ["two", "one", "three"]
    await auth_client.patch(f"{ROUTE}/{three}", json={"description": "Done."}, headers=ALICE)
    assert await _board(auth_client) == ["two", "one", "three"]
    await auth_client.patch(f"{ROUTE}/{three}", json={"state": "active"}, headers=ALICE)
    assert await _board(auth_client) == ["three", "two", "one"]
    # The state it already has moves nothing.
    await auth_client.patch(f"{ROUTE}/{one}", json={"state": "active"}, headers=ALICE)
    assert await _board(auth_client) == ["three", "two", "one"]

    unassigned = await auth_client.patch(
        f"{ROUTE}/{three}", json={"manager_id": None}, headers=ALICE
    )
    to_bobs_manager = await auth_client.patch(
        f"{ROUTE}/{three}", json={"manager_id": bobs_manager}, headers=ALICE
    )
    by_bob = await auth_client.patch(f"{ROUTE}/{three}", json={"title": "taken"}, headers=BOB)

    assert unassigned.json()["manager_id"] is None
    assert unassigned.json()["description"] == "Done."
    assert to_bobs_manager.status_code == 404
    assert by_bob.status_code == 404


async def test_batch_get_skips_unknown_foreign_and_malformed_ids(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id, _ = _manager(db_uri)
    bobs_manager, _ = _manager(db_uri, owner="bob@example.com")
    first = await _create(auth_client, manager_id, "first")
    second = await _create(auth_client, manager_id, "second")
    bobs_task = await _create(auth_client, bobs_manager, "bob's", headers=BOB)

    response = await auth_client.post(
        f"{ROUTE}/batch",
        json={"task_ids": [first, bobs_task, "not-an-id", second, first]},
        headers=ALICE,
    )

    assert [task["title"] for task in response.json()["data"]] == ["first", "second"]


async def test_move_to_end_archive_and_permanent_delete(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id, _ = _manager(db_uri)
    await _create(auth_client, manager_id, "one")
    two = await _create(auth_client, manager_id, "two")

    await auth_client.post(f"{ROUTE}/{two}/move-to-queue-end", headers=ALICE)
    assert await _board(auth_client) == ["one", "two"]

    archived = await auth_client.delete(f"{ROUTE}/{two}", headers=ALICE)
    assert archived.json() == {
        "object": "agent.task",
        "id": two,
        "deleted": True,
        "permanent": False,
    }
    assert (await auth_client.get(f"{ROUTE}/{two}", headers=ALICE)).json()["state"] == "archived"

    assert (await auth_client.delete(f"{ROUTE}/{two}/permanent", headers=BOB)).status_code == 404
    deleted = await auth_client.delete(f"{ROUTE}/{two}/permanent", headers=ALICE)
    assert deleted.json()["permanent"] is True
    assert (await auth_client.get(f"{ROUTE}/{two}", headers=ALICE)).status_code == 404
