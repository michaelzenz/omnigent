"""Tests for the GlobalHub FYI cluster routes."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore

ALICE_ID = "alice@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": "bob@example.com"}
BOARD = "/v1/agent-tasks/board/pending"
FILE = "/v1/task-events/fyi-clusters"


async def _event(client: httpx.AsyncClient, title: str) -> str:
    response = await client.post(
        "/v1/task-events", json={"event_type": "slack.message", "title": title}, headers=ALICE
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _file(
    client: httpx.AsyncClient,
    event_ids: list[str],
    headline: str,
    headers: dict[str, str] = ALICE,
    **fields: Any,
) -> httpx.Response:
    return await client.post(
        FILE, json={"event_ids": event_ids, "headline": headline, **fields}, headers=headers
    )


async def test_a_manager_files_events_into_a_cluster_on_the_board(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id = uuid.uuid4().hex
    manager_session = SqlAlchemyConversationStore(db_uri).create_conversation(title="Storage")
    SqlAlchemyManagerStore(db_uri).create(
        manager_id, owner_user_id=ALICE_ID, title="Storage", conversation_id=manager_session.id
    )
    waiting = await _event(auth_client, "Channel chatter")
    routed = await _event(auth_client, "Release thread")
    routing = await auth_client.post(
        "/v1/task-events/batch-route-manager",
        json={"event_ids": [routed], "manager_id": manager_id},
        headers=ALICE,
    )
    later = await _event(auth_client, "More chatter")

    created = await _file(auth_client, [waiting, routed], "Chatter", rationale="No action needed")
    cluster_id = created.json()["id"]
    extended = await _file(auth_client, [later, waiting], "Release chatter", cluster_id=cluster_id)
    board = (await auth_client.get(BOARD, headers=ALICE)).json()

    assert routing.json()["data"][0]["state"] == "routed"
    assert {event["id"]: event["state"] for event in created.json()["events"]} == {
        waiting: "fyi",
        routed: "fyi",
    }
    assert (extended.json()["headline"], extended.json()["rationale"]) == (
        "Release chatter",
        "No action needed",
    )
    assert [
        (cluster["id"], {event["id"] for event in cluster["events"]}) for cluster in board["fyi"]
    ] == [(cluster_id, {waiting, routed, later})]
    assert (await auth_client.get(BOARD, headers=BOB)).json()["fyi"] == []


async def test_filing_refuses_settled_events_and_clusters_the_caller_cannot_use(
    auth_client: httpx.AsyncClient,
) -> None:
    waiting = await _event(auth_client, "Channel chatter")
    handled = await _event(auth_client, "Old thread")
    await auth_client.post(f"/v1/task-events/{handled}/dismiss", headers=ALICE)
    stale = await _event(auth_client, "Stale chatter")
    dismissed_cluster = (await _file(auth_client, [stale], "Stale")).json()["id"]
    await auth_client.post(f"/v1/fyi-clusters/{dismissed_cluster}/resolve", headers=ALICE)

    statuses = [
        (await _file(auth_client, [waiting, handled], "Chatter")).status_code,
        (await _file(auth_client, [waiting, uuid.uuid4().hex], "Chatter")).status_code,
        (await _file(auth_client, [waiting], "Chatter", cluster_id=dismissed_cluster)).status_code,
        (
            await _file(
                auth_client, [waiting], "Chatter", headers=BOB, cluster_id=dismissed_cluster
            )
        ).status_code,
        (await _file(auth_client, [waiting], "  ")).status_code,
    ]

    assert statuses == [409, 404, 409, 404, 422]
    assert (await auth_client.get(BOARD, headers=ALICE)).json()["fyi"] == []


async def test_resolving_a_cluster_dismisses_it_and_its_events_once(
    auth_client: httpx.AsyncClient,
) -> None:
    event_id = await _event(auth_client, "Channel chatter")
    cluster_id = (await _file(auth_client, [event_id], "Chatter")).json()["id"]
    resolve = f"/v1/fyi-clusters/{cluster_id}/resolve"

    by_bob = await auth_client.post(resolve, headers=BOB)
    first = await auth_client.post(resolve, json={"resolution": "dismiss_fyi"}, headers=ALICE)
    again = await auth_client.post(resolve, headers=ALICE)

    assert by_bob.status_code == 404
    assert (first.json()["state"], [event["state"] for event in first.json()["events"]]) == (
        "dismissed",
        ["dismissed"],
    )
    assert again.json()["resolved_at"] == first.json()["resolved_at"]
    assert (await auth_client.get(BOARD, headers=ALICE)).json()["fyi"] == []
