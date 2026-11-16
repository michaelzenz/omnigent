"""Tests for the GlobalHub task event routes (``/v1/task-events``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.task_subscription_store.sqlalchemy_store import (
    SqlAlchemyTaskSubscriptionStore,
)

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}
ROUTE = "/v1/task-events"
PR = "https://github.com/example-org/repo/pull/12"
OTHER_PR = "https://github.com/example-org/repo/pull/13"


def _manager(db_uri: str, owner: str = ALICE_ID) -> str:
    manager_id = uuid.uuid4().hex
    SqlAlchemyManagerStore(db_uri).create(manager_id, owner_user_id=owner, title="Storage")
    return manager_id


def _task(db_uri: str, manager_id: str | None = None) -> str:
    return (
        SqlAlchemyTaskStore(db_uri)
        .create(
            uuid.uuid4().hex,
            owner_user_id=ALICE_ID,
            title="PR 12",
            goal="Merge it",
            manager_id=manager_id,
        )
        .id
    )


def _session(db_uri: str) -> str:
    session = SqlAlchemyConversationStore(db_uri).create_conversation(title="Fix S3")
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(ALICE_ID)
    permissions.grant(ALICE_ID, session.id, LEVEL_OWNER)
    return session.id


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
    # Only the server records session events: by their type or their source.
    session_events = [
        await auth_client.post(ROUTE, json=body, headers=ALICE)
        for body in (
            {"event_type": "session.turn.finished", "title": "Turn"},
            {"event_type": "pr.comment", "title": "Turn", "source": "session", "source_key": "a"},
        )
    ]

    assert (first["state"], first["payload"], first["tags"], first["manager_id"]) == (
        "unrouted",
        {"body": "Please rename"},
        tags,
        None,
    )
    assert repeated["id"] == first["id"]
    assert next_comment["id"] != first["id"]
    assert [response.status_code for response in session_events] == [400, 400]


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


async def test_an_event_goes_to_each_live_task_that_follows_its_key(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id = _manager(db_uri)
    managed, unmanaged = _task(db_uri, manager_id=manager_id), _task(db_uri)
    for task_id in (managed, unmanaged):
        SqlAlchemyTaskSubscriptionStore(db_uri).subscribe(
            uuid.uuid4().hex, owner_user_id=ALICE_ID, task_id=task_id, source_key=PR
        )

    first = await _create(auth_client, "CI failed", source="github", source_key=PR)
    other = await _create(auth_client, "Other PR", source="github", source_key=OTHER_PR)

    events = SqlAlchemyTaskEventStore(db_uri).list(owner_user_id=ALICE_ID)
    copies = {(event.task_id, event.manager_id, event.state) for event in events}
    assert copies == {
        (managed, manager_id, "routed"),
        (unmanaged, None, "unrouted"),
        (None, None, "unrouted"),
    }
    assert first["task_id"] in (managed, unmanaged)
    assert (other["task_id"], other["state"]) == (None, "unrouted")


async def test_assigning_an_event_makes_its_task_follow_the_key_and_handles_it(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_id = _manager(db_uri)
    task_id = _task(db_uri, manager_id=manager_id)
    comment = await _create(auth_client, "Comment", source="github", source_key=PR)
    session_id = _session(db_uri)
    turn = SqlAlchemyTaskEventStore(db_uri).create(
        uuid.uuid4().hex,
        owner_user_id=ALICE_ID,
        event_type="session.turn.finished",
        title="Session turn finished: Fix S3",
        source="session",
        source_key=session_id,
    )

    assigned = await auth_client.post(
        f"{ROUTE}/{comment['id']}/assign", json={"task_id": task_id}, headers=ALICE
    )
    worker = await auth_client.post(
        f"{ROUTE}/{turn.id}/assign",
        json={"task_id": task_id, "title": "Fixing S3 retries"},
        headers=ALICE,
    )
    follow_up = await _create(
        auth_client, "New commit", source="github", source_key=PR, source_offset="commit-2"
    )
    roster = await auth_client.get(f"/v1/agent-tasks/{task_id}/workers", headers=ALICE)

    body = assigned.json()
    assert (body["event"]["state"], body["event"]["task_id"], body["worker_id"]) == (
        "dismissed",
        task_id,
        None,
    )
    assert body["subscription_id"] is not None
    assert (worker.json()["subscription_id"], worker.json()["event"]["task_id"]) == (
        None,
        task_id,
    )
    assert [(row["id"], row["session_id"], row["title"]) for row in roster.json()["data"]] == [
        (worker.json()["worker_id"], session_id, "Fixing S3 retries")
    ]
    # Later events with the key go straight to the task's manager.
    assert (follow_up["task_id"], follow_up["manager_id"], follow_up["state"]) == (
        task_id,
        manager_id,
        "routed",
    )


async def test_assigning_needs_the_callers_event_and_task_and_no_fyi_filing(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id = _task(db_uri)
    event_id = (await _create(auth_client, "Comment", source="github", source_key=PR))["id"]
    filed_id = (await _create(auth_client, "Release notes"))["id"]
    await auth_client.post(
        f"{ROUTE}/fyi-clusters",
        json={"event_ids": [filed_id], "headline": "Release"},
        headers=ALICE,
    )

    statuses = [
        (
            await auth_client.post(
                f"{ROUTE}/{event_id}/assign", json={"task_id": task_id}, headers=BOB
            )
        ).status_code,
        (
            await auth_client.post(
                f"{ROUTE}/{event_id}/assign", json={"task_id": uuid.uuid4().hex}, headers=ALICE
            )
        ).status_code,
        (
            await auth_client.post(
                f"{ROUTE}/{filed_id}/assign", json={"task_id": task_id}, headers=ALICE
            )
        ).status_code,
    ]

    assert statuses == [404, 404, 409]
    waiting = SqlAlchemyTaskEventStore(db_uri).get(event_id, owner_user_id=ALICE_ID)
    assert waiting is not None and (waiting.state, waiting.task_id) == ("unrouted", None)
