"""Tests for the GlobalHub task routes (``/v1/agent-tasks``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.entities.task import MAX_TASK_INTERNAL_NOTE_CHARS
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.subscription_store.sqlalchemy_store import SqlAlchemySubscriptionStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

ALICE_ID = "alice@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
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
        json={"manager_id": manager_id, "title": title, **fields},
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
        ROUTE, json={"manager_id": bobs_manager, "title": "x"}, headers=ALICE
    )
    born_idle = await auth_client.post(
        ROUTE,
        json={"manager_id": manager_id, "title": "x", "state": "idle"},
        headers=ALICE,
    )
    blank_title = await auth_client.post(
        ROUTE, json={"manager_id": manager_id, "title": "  "}, headers=ALICE
    )
    long_note = await auth_client.post(
        ROUTE,
        json={
            "manager_id": manager_id,
            "title": "x",
            "internal_note": "n" * (MAX_TASK_INTERNAL_NOTE_CHARS + 1),
        },
        headers=ALICE,
    )
    with_goal = await auth_client.post(
        ROUTE, json={"manager_id": manager_id, "title": "x", "goal": "y"}, headers=ALICE
    )

    assert await _board(auth_client) == ["second", "first"]
    assert await _board(auth_client, BOB) == []
    assert with_bobs_manager.status_code == 404
    assert born_idle.status_code == 422
    assert blank_title.status_code == 422
    assert long_note.status_code == 422
    assert with_goal.status_code == 422, "a task has no goal field"


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


async def test_move_to_end_and_permanent_delete(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id, _ = _manager(db_uri)
    await _create(auth_client, manager_id, "one")
    two = await _create(auth_client, manager_id, "two")

    await auth_client.post(f"{ROUTE}/{two}/move-to-queue-end", headers=ALICE)
    assert await _board(auth_client) == ["one", "two"]

    assert (await auth_client.delete(f"{ROUTE}/{two}/permanent", headers=BOB)).status_code == 404
    deleted = await auth_client.delete(f"{ROUTE}/{two}/permanent", headers=ALICE)
    assert deleted.json() == {"object": "agent.task", "id": two, "deleted": True}
    assert (await auth_client.get(f"{ROUTE}/{two}", headers=ALICE)).status_code == 404


async def test_the_manager_of_a_live_task_follows_the_sessions_of_its_workers(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    storage, storage_session = _manager(db_uri)
    docs, docs_session = _manager(db_uri)
    task = await _create(auth_client, storage, "S3 retries")
    other_task = await _create(auth_client, storage, "Dashboards")
    solo_worker, shared_worker = uuid.uuid4().hex, uuid.uuid4().hex
    workers = SqlAlchemyWorkerStore(db_uri)
    for task_id, session_id in (
        (task, solo_worker),
        (task, shared_worker),
        (other_task, shared_worker),
    ):
        workers.create(
            uuid.uuid4().hex, owner_user_id=ALICE_ID, task_id=task_id, session_id=session_id
        )
    subscriptions = SqlAlchemySubscriptionStore(db_uri)

    def followed_by(manager_session: str) -> set[str]:
        return {
            subscription.source_key
            for subscription in subscriptions.list_for_session(
                manager_session, owner_user_id=ALICE_ID
            )
        }

    await auth_client.patch(f"{ROUTE}/{task}", json={"state": "archived"}, headers=ALICE)
    while_archived = followed_by(storage_session)
    await auth_client.patch(f"{ROUTE}/{task}", json={"state": "active"}, headers=ALICE)
    revived = followed_by(storage_session)
    await auth_client.patch(f"{ROUTE}/{task}", json={"manager_id": docs}, headers=ALICE)
    reassigned = (followed_by(storage_session), followed_by(docs_session))
    await auth_client.delete(f"{ROUTE}/{task}/permanent", headers=ALICE)
    deleted = followed_by(docs_session)

    assert while_archived == set()
    assert revived == {solo_worker, shared_worker}
    assert reassigned == ({shared_worker}, {solo_worker, shared_worker})
    assert deleted == set()
