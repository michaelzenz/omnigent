"""Tests for the GlobalHub task event routes (``/v1/task-events``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}
ROUTE = "/v1/task-events"


def _manager(db_uri: str, owner: str = ALICE_ID) -> str:
    manager_id = uuid.uuid4().hex
    SqlAlchemyManagerStore(db_uri).create(manager_id, owner_user_id=owner, title="Storage")
    return manager_id


async def _create(client: httpx.AsyncClient, title: str, **fields: Any) -> dict[str, Any]:
    response = await client.post(
        ROUTE, json={"event_type": "github.pr.comment", "title": title, **fields}, headers=ALICE
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_created_events_wait_for_the_broker_and_a_repeated_report_is_kept_once(
    auth_client: httpx.AsyncClient,
) -> None:
    report = {
        "source": "github",
        "source_key": "https://example.com/pull/1",
        "source_offset": "comment-1",
    }
    tags = [{"tag_type": "repo", "tag": "example-org/storage"}]

    first = await _create(
        auth_client, "Review comment", payload={"body": "Please rename"}, tags=tags, **report
    )
    repeated = await _create(auth_client, "Review comment again", **report)
    next_comment = await _create(
        auth_client, "Second comment", **{**report, "source_offset": "comment-2"}
    )
    session_event = await auth_client.post(
        ROUTE, json={"event_type": "session.turn.finished", "title": "Turn"}, headers=ALICE
    )

    assert (first["state"], first["payload"], first["tags"], first["manager_id"]) == (
        "unrouted",
        {"body": "Please rename"},
        tags,
        None,
    )
    assert repeated["id"] == first["id"]
    assert next_comment["id"] != first["id"]
    assert session_event.status_code == 400


async def test_the_broker_routes_waiting_events_to_one_of_the_callers_managers(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id, other_manager_id = _manager(db_uri), _manager(db_uri)
    bobs_manager_id = _manager(db_uri, owner=BOB_ID)
    waiting = (await _create(auth_client, "Review comment"))["id"]
    handled = (await _create(auth_client, "Old comment"))["id"]
    await auth_client.post(f"{ROUTE}/{handled}/dismiss", headers=ALICE)

    async def route(
        event_ids: list[str], manager: str, headers: dict[str, str] = ALICE
    ) -> httpx.Response:
        return await auth_client.post(
            f"{ROUTE}/batch-route-manager",
            json={"event_ids": event_ids, "manager_id": manager},
            headers=headers,
        )

    refused = [
        (await route([waiting], bobs_manager_id)).status_code,
        (await route([waiting, uuid.uuid4().hex], manager_id)).status_code,
        (await route([waiting, handled], manager_id)).status_code,
        (await route([waiting], manager_id, headers=BOB)).status_code,
    ]
    routed = await route([waiting], manager_id)
    retried = await route([waiting], manager_id)
    elsewhere = await route([waiting], other_manager_id)

    assert refused == [404, 404, 409, 404]
    assert [
        (event["id"], event["state"], event["manager_id"]) for event in routed.json()["data"]
    ] == [(waiting, "routed", manager_id)]
    assert (retried.status_code, elsewhere.status_code) == (200, 409)


async def test_dismiss_settles_only_the_callers_events_and_repeats_safely(
    auth_client: httpx.AsyncClient,
) -> None:
    event_id = (await _create(auth_client, "Review comment"))["id"]

    by_bob = await auth_client.post(f"{ROUTE}/{event_id}/dismiss", headers=BOB)
    first = await auth_client.post(f"{ROUTE}/{event_id}/dismiss", headers=ALICE)
    again = await auth_client.post(f"{ROUTE}/{event_id}/dismiss", headers=ALICE)

    assert by_bob.status_code == 404
    assert (first.json()["state"], again.json()["state"]) == ("dismissed", "dismissed")
    assert again.json()["processed_at"] == first.json()["processed_at"]
