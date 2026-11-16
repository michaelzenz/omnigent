"""Tests for the routes of the keys that GlobalHub tasks follow (``.../subscriptions``)."""

from __future__ import annotations

import uuid

import httpx

from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

ALICE_ID = "alice@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": "bob@example.com"}
PR = "https://github.com/example-org/repo/pull/12"


def _task(db_uri: str, state: str = "active") -> str:
    return (
        SqlAlchemyTaskStore(db_uri)
        .create(uuid.uuid4().hex, owner_user_id=ALICE_ID, title="PR 12", state=state)
        .id
    )


async def test_a_task_subscribes_to_a_key_once_and_unsubscribes(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id, archived_id = _task(db_uri), _task(db_uri, state="archived")
    path = f"/v1/agent-tasks/{task_id}/subscriptions"
    body = {"source_key": PR, "source": "github"}

    created = (await auth_client.post(path, json=body, headers=ALICE)).json()
    again = (await auth_client.post(path, json={"source_key": PR}, headers=ALICE)).json()
    await auth_client.post(
        f"/v1/agent-tasks/{archived_id}/subscriptions", json={"source_key": PR}, headers=ALICE
    )
    listed = (await auth_client.get(path, headers=ALICE)).json()["data"]
    live = (await auth_client.get("/v1/agent-tasks/subscriptions", headers=ALICE)).json()["data"]
    deleted = await auth_client.delete(f"{path}/{created['id']}", headers=ALICE)
    after = (await auth_client.get(path, headers=ALICE)).json()["data"]

    assert (created["source_key"], created["source"], created["task_id"]) == (
        PR,
        "github",
        task_id,
    )
    assert again["id"] == created["id"]
    assert [row["id"] for row in listed] == [created["id"]]
    # The archived task's subscription is not listed with the live ones.
    assert [row["id"] for row in live] == [created["id"]]
    assert deleted.json() == {
        "object": "agent.task.subscription",
        "id": created["id"],
        "task_id": task_id,
        "deleted": True,
    }
    assert after == []


async def test_subscriptions_need_the_callers_own_task_and_a_storable_key(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id = _task(db_uri)
    path = f"/v1/agent-tasks/{task_id}/subscriptions"
    created = (await auth_client.post(path, json={"source_key": PR}, headers=ALICE)).json()

    statuses = [
        (await auth_client.post(path, json={"source_key": PR}, headers=BOB)).status_code,
        (await auth_client.get(path, headers=BOB)).status_code,
        (await auth_client.delete(f"{path}/{created['id']}", headers=BOB)).status_code,
        (await auth_client.post(path, json={"source_key": "k" * 600}, headers=ALICE)).status_code,
        (await auth_client.post(path, json={"source_key": "  "}, headers=ALICE)).status_code,
    ]

    assert statuses == [404, 404, 404, 422, 422]
    bobs_live = await auth_client.get("/v1/agent-tasks/subscriptions", headers=BOB)
    assert bobs_live.json()["data"] == []
