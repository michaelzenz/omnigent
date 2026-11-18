"""Tests for the GlobalHub task event routes (``/v1/task-events``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.entities import SESSION_EVENT_SOURCE
from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.subscription_store.sqlalchemy_store import SqlAlchemySubscriptionStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}
ROUTE = "/v1/task-events"
PULL_REQUEST = "github.pull_request"
PR = "https://github.com/example-org/repo/pull/12"
OTHER_PR = "https://github.com/example-org/repo/pull/13"
NEW_PR = "https://github.com/example-org/repo/pull/14"


def _manager(db_uri: str, owner: str = ALICE_ID, session_id: str | None = None) -> str:
    manager_id = uuid.uuid4().hex
    SqlAlchemyManagerStore(db_uri).create(
        manager_id, owner_user_id=owner, title="Storage", conversation_id=session_id
    )
    return manager_id


def _task(db_uri: str, manager_id: str | None = None) -> str:
    return (
        SqlAlchemyTaskStore(db_uri)
        .create(
            uuid.uuid4().hex,
            owner_user_id=ALICE_ID,
            title="PR 12",
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


def _follow(db_uri: str, session_id: str, source_key: str) -> None:
    SqlAlchemySubscriptionStore(db_uri).subscribe(
        owner_user_id=ALICE_ID,
        session_id=session_id,
        source_type=PULL_REQUEST,
        source_key=source_key,
    )


async def _create(client: httpx.AsyncClient, title: str, **fields: Any) -> dict[str, Any]:
    response = await client.post(
        ROUTE, json={"event_type": "github.pr.comment", "title": title, **fields}, headers=ALICE
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_created_events_wait_for_the_broker_and_a_repeated_report_is_kept_once(
    auth_client: httpx.AsyncClient,
) -> None:
    report = {"source": PULL_REQUEST, "source_key": PR, "source_offset": "comment-1"}
    tags = [{"tag_type": "repo", "tag": "example-org/storage"}]

    first = await _create(
        auth_client, "Review comment", payload={"body": "Please rename"}, tags=tags, **report
    )
    repeated = await _create(
        auth_client,
        "Review comment again",
        **{**report, "source": " GitHub.Pull_Request ", "source_key": f"{PR}/#issuecomment-1"},
    )
    next_comment = await _create(
        auth_client, "Second comment", **{**report, "source_offset": "comment-2"}
    )
    refused = [
        await auth_client.post(ROUTE, json=body, headers=ALICE)
        for body in (
            {"event_type": "session.turn.finished", "title": "Turn"},
            {
                "event_type": "pr.comment",
                "title": "Turn",
                "source": SESSION_EVENT_SOURCE,
                "source_key": "a",
            },
            {"event_type": "pr.comment", "title": "Comment", "source": "github", "source_key": PR},
        )
    ]

    assert (first["state"], first["payload"], first["tags"], first["target_session_id"]) == (
        "unrouted",
        {"body": "Please rename"},
        tags,
        None,
    )
    assert (first["source"], first["source_key"]) == (PULL_REQUEST, PR)
    assert repeated["id"] == first["id"]
    assert next_comment["id"] != first["id"]
    assert [response.status_code for response in refused] == [400, 400, 400]


async def test_an_event_goes_to_each_session_that_follows_its_key_but_not_its_reporter(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    follower, other_follower, reporter = (uuid.uuid4().hex for _ in range(3))
    for session_id in (follower, other_follower, reporter):
        _follow(db_uri, session_id, PR)
    _follow(db_uri, reporter, OTHER_PR)

    first = await _create(
        auth_client,
        "CI failed",
        source=PULL_REQUEST,
        source_key=f"{PR}/",
        source_session_id=reporter,
    )
    own = await _create(
        auth_client,
        "Pushed a commit",
        source=PULL_REQUEST,
        source_key=OTHER_PR,
        source_session_id=reporter,
    )
    unfollowed = await _create(auth_client, "New PR", source=PULL_REQUEST, source_key=NEW_PR)

    events = SqlAlchemyTaskEventStore(db_uri).list(owner_user_id=ALICE_ID)
    copies = sorted(
        (event.source_key, event.target_session_id or "", event.state) for event in events
    )
    assert copies == sorted(
        [
            (PR, follower, "routed"),
            (PR, other_follower, "routed"),
            (OTHER_PR, reporter, "dismissed"),
            (NEW_PR, "", "unrouted"),
        ]
    )
    assert first["target_session_id"] in {follower, other_follower}
    assert (own["state"], own["target_session_id"]) == ("dismissed", reporter)
    assert (unfollowed["state"], unfollowed["target_session_id"]) == ("unrouted", None)


async def test_the_broker_routes_waiting_events_to_a_managers_session_without_following_them(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_session = _session(db_uri)
    manager_id = _manager(db_uri, session_id=manager_session)
    other_manager_id = _manager(db_uri, session_id=_session(db_uri))
    sessionless_id = _manager(db_uri)
    gone_session_id = _manager(db_uri, session_id=uuid.uuid4().hex)
    archived_session = _session(db_uri)
    SqlAlchemyConversationStore(db_uri).update_conversation(archived_session, archived=True)
    archived_session_id = _manager(db_uri, session_id=archived_session)
    bobs_manager_id = _manager(db_uri, owner=BOB_ID, session_id=uuid.uuid4().hex)
    review = await _create(auth_client, "Review comment", source=PULL_REQUEST, source_key=PR)
    waiting = review["id"]
    chatter = (await _create(auth_client, "Channel chatter"))["id"]
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
        (await route([waiting], sessionless_id)).status_code,
        (await route([waiting], gone_session_id)).status_code,
        (await route([waiting], archived_session_id)).status_code,
    ]
    routed = await route([waiting, chatter], manager_id)
    retried = await route([waiting], manager_id)
    elsewhere = await route([waiting], other_manager_id)
    follow_up = await _create(
        auth_client, "CI failed", source=PULL_REQUEST, source_key=PR, source_offset="check-2"
    )

    assert refused == [404, 404, 409, 404, 409, 409, 409]
    assert [
        (event["id"], event["state"], event["target_session_id"])
        for event in routed.json()["data"]
    ] == [(waiting, "routed", manager_session), (chatter, "routed", manager_session)]
    assert (retried.status_code, elsewhere.status_code) == (200, 409)
    assert (
        SqlAlchemySubscriptionStore(db_uri).list_for_session(
            manager_session, owner_user_id=ALICE_ID
        )
        == []
    )
    assert (follow_up["state"], follow_up["target_session_id"]) == ("unrouted", None)
    assert SqlAlchemyAgentQueueStore(db_uri).list(owner_user_id=ALICE_ID) == []


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


async def test_assigning_a_session_event_makes_a_worker_that_the_tasks_manager_follows(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    manager_session = uuid.uuid4().hex
    task_id = _task(db_uri, manager_id=_manager(db_uri, session_id=manager_session))
    comment = await _create(auth_client, "Comment", source=PULL_REQUEST, source_key=PR)
    session_id = _session(db_uri)
    events = SqlAlchemyTaskEventStore(db_uri)

    def turn(source_key: str) -> str:
        return events.create(
            uuid.uuid4().hex,
            owner_user_id=ALICE_ID,
            event_type="session.turn.finished",
            title="Session turn finished: Fix S3",
            source=SESSION_EVENT_SOURCE,
            source_key=source_key,
        ).id

    assigned = await auth_client.post(
        f"{ROUTE}/{comment['id']}/assign", json={"task_id": task_id}, headers=ALICE
    )
    worker = await auth_client.post(
        f"{ROUTE}/{turn(session_id)}/assign",
        json={"task_id": task_id, "title": "Fixing S3 retries"},
        headers=ALICE,
    )
    gone = await auth_client.post(
        f"{ROUTE}/{turn(uuid.uuid4().hex)}/assign", json={"task_id": task_id}, headers=ALICE
    )
    roster = await auth_client.get(f"/v1/agent-tasks/{task_id}/workers", headers=ALICE)

    body = assigned.json()
    assert body.keys() == {"object", "event", "worker_id"}
    assert (body["event"]["state"], body["event"]["task_id"], body["worker_id"]) == (
        "dismissed",
        task_id,
        None,
    )
    assert (gone.json()["event"]["task_id"], gone.json()["worker_id"]) == (task_id, None)
    assert [(row["id"], row["session_id"], row["title"]) for row in roster.json()["data"]] == [
        (worker.json()["worker_id"], session_id, "Fixing S3 retries")
    ]
    subscriptions = SqlAlchemySubscriptionStore(db_uri)
    assert subscriptions.subscribers(owner_user_id=ALICE_ID, source_key=session_id) == [
        manager_session
    ]
    assert subscriptions.subscribers(owner_user_id=ALICE_ID, source_key=PR) == []


async def test_assigning_needs_the_callers_event_and_task_and_no_fyi_filing(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id = _task(db_uri)
    event_id = (await _create(auth_client, "Comment", source=PULL_REQUEST, source_key=PR))["id"]
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
