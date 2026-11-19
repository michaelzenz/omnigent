"""Tests for the routes of the sources that sessions follow, and of the source type watchers."""

from __future__ import annotations

import uuid

import httpx

from omnigent.agent_tasks.session_labels import (
    CUSTODIAN_ROLE_VALUE,
    MANAGER_ROLE_VALUE,
    ROLE_LABEL,
)
from omnigent.entities import SESSION_EVENT_SOURCE
from omnigent.runner.identity import (
    ORCHESTRATION_SOURCE_HEADER,
    RUNNER_TUNNEL_TOKEN_HEADER,
    token_bound_runner_id,
)
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.subscription_store.sqlalchemy_store import SqlAlchemySubscriptionStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore
from omnigent.stores.watch_item_store import WatchObservation
from omnigent.stores.watch_item_store.sqlalchemy_store import SqlAlchemyWatchItemStore
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
WATCHERS = "/v1/agent-tasks/watchers"
SOURCE_TYPES = "/v1/agent-tasks/source-types"
PULL_REQUEST = "github.pull_request"
TICKET = "jira.issue"
PR = "https://github.com/example-org/repo/pull/12"
OTHER_PR = "https://github.com/example-org/repo/pull/13"
TICKET_PAGE = "https://example.com/browse/PROJ-12"


def _session(
    db_uri: str,
    owner: str = ALICE_ID,
    labels: dict[str, str] | None = None,
    placement: GlobalHubPlacement | None = None,
) -> str:
    session = SqlAlchemyConversationStore(db_uri).create_conversation(
        title="Fix S3",
        labels=labels,
        host_id=placement.host_id if placement else None,
        workspace=placement.workspace if placement else None,
    )
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(owner)
    permissions.grant(owner, session.id, LEVEL_OWNER)
    return session.id


def _subscriptions(session_id: str) -> str:
    return f"/v1/sessions/{session_id}/subscriptions"


def _runner_headers(db_uri: str, session_id: str, token: str) -> dict[str, str]:
    SqlAlchemyConversationStore(db_uri).set_runner_id(session_id, token_bound_runner_id(token))
    return {**ALICE, ORCHESTRATION_SOURCE_HEADER: session_id, RUNNER_TUNNEL_TOKEN_HEADER: token}


async def test_a_session_follows_a_source_once_and_stops_following_it(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    session_id = _session(db_uri)
    path = _subscriptions(session_id)

    created = await auth_client.post(
        path,
        json={"source_type": " GitHub.Pull_Request ", "source_key": f"{PR}/", "note": "Opened"},
        headers=ALICE,
    )
    again = await auth_client.post(
        path,
        json={"source_type": PULL_REQUEST, "source_key": PR, "note": "Reviewing"},
        headers=ALICE,
    )
    await auth_client.post(
        path,
        json={"source_type": TICKET, "source_key": "PROJ-12", "url": TICKET_PAGE},
        headers=ALICE,
    )
    listed = (await auth_client.get(path, headers=ALICE)).json()["data"]
    removed = await auth_client.post(
        f"{path}/remove", json={"source_key": f"{PR}#top"}, headers=ALICE
    )
    removed_again = await auth_client.post(
        f"{path}/remove", json={"source_key": PR}, headers=ALICE
    )
    after = (await auth_client.get(path, headers=ALICE)).json()["data"]

    body = created.json()
    assert (body["object"], body["session_id"], body["source_type"], body["source_key"]) == (
        "session.subscription",
        session_id,
        PULL_REQUEST,
        PR,
    )
    assert (again.json()["id"], again.json()["note"]) == (body["id"], "Reviewing")
    assert sorted((row["source_type"], row["source_key"], row["url"]) for row in listed) == [
        (PULL_REQUEST, PR, None),
        (TICKET, "PROJ-12", TICKET_PAGE),
    ]
    assert (removed.json()["removed"], removed_again.json()["removed"]) == (True, False)
    assert [row["source_key"] for row in after] == ["PROJ-12"]


async def test_following_needs_edit_access_a_live_session_and_a_type_the_server_does_not_own(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    session_id = _session(db_uri)
    archived_id = _session(db_uri)
    SqlAlchemyConversationStore(db_uri).update_conversation(archived_id, archived=True)
    bobs_id = _session(db_uri, owner=BOB_ID)
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(BOB_ID)
    permissions.grant(BOB_ID, session_id, LEVEL_READ)
    path = _subscriptions(session_id)

    def follow(source_type: str, source_key: str = PR) -> dict[str, str]:
        return {"source_type": source_type, "source_key": source_key}

    responses = [
        await auth_client.post(path, json=follow(SESSION_EVENT_SOURCE, bobs_id), headers=ALICE),
        await auth_client.post(path, json=follow("github"), headers=ALICE),
        await auth_client.post(
            path,
            json={**follow(TICKET, "PROJ-12"), "url": "javascript:alert(1)"},
            headers=ALICE,
        ),
        await auth_client.post(path, json=follow(PULL_REQUEST, "k" * 600), headers=ALICE),
        await auth_client.post(
            _subscriptions(archived_id), json=follow(PULL_REQUEST), headers=ALICE
        ),
        await auth_client.post(_subscriptions(bobs_id), json=follow(PULL_REQUEST), headers=ALICE),
        await auth_client.post(
            _subscriptions(uuid.uuid4().hex), json=follow(PULL_REQUEST), headers=ALICE
        ),
        await auth_client.post(path, json=follow(PULL_REQUEST), headers=BOB),
        await auth_client.post(f"{path}/remove", json={"source_key": PR}, headers=BOB),
        await auth_client.get(path, headers=BOB),
    ]

    assert [response.status_code for response in responses] == [
        400,
        400,
        400,
        422,
        404,
        404,
        404,
        403,
        403,
        200,
    ]
    assert (await auth_client.get(path, headers=ALICE)).json()["data"] == []


async def test_a_new_source_type_asks_the_custodian_once_to_assign_it_to_a_watcher(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    SqlAlchemyAgentStore(db_uri).create(
        uuid.uuid4().hex, "onih-custodian", "bundles/onih-custodian.tar.gz"
    )
    settings = UserSettingsStore(db_uri)
    settings.save_globalhub_placement(
        GlobalHubPlacement(host_id=HOST_ID, workspace="/Users/alice/globalhub"), user_id=ALICE_ID
    )
    path = _subscriptions(_session(db_uri))

    responses = [
        await auth_client.post(
            path, json={"source_type": source_type, "source_key": source_key}, headers=ALICE
        )
        for source_type, source_key in (
            (PULL_REQUEST, PR),
            (PULL_REQUEST, OTHER_PR),
            (TICKET, "PROJ-12"),
        )
    ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    custodian = settings.get_globalhub_role_session("custodian", user_id=ALICE_ID)
    queued = SqlAlchemyAgentQueueStore(db_uri).list(owner_user_id=ALICE_ID)
    assert [(item.kind, item.target_session_id, item.state) for item in queued] == [
        ("source_types", custodian, "queued")
    ]
    fallback = SqlAlchemyWatcherStore(db_uri).get_builtin(owner_user_id=ALICE_ID)
    assert fallback is not None and fallback.name == "Fallback watcher"


async def test_source_types_list_each_type_in_use_with_its_watcher(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    watcher = (await auth_client.post(WATCHERS, json={"name": "PR watcher"}, headers=ALICE)).json()
    first, second = _session(db_uri), _session(db_uri)
    subscriptions = SqlAlchemySubscriptionStore(db_uri)
    for session_id, source_type, source_key in (
        (first, PULL_REQUEST, PR),
        (second, PULL_REQUEST, PR),
        (second, TICKET, "PROJ-12"),
        (first, SESSION_EVENT_SOURCE, second),
    ):
        subscriptions.subscribe(
            owner_user_id=ALICE_ID,
            session_id=session_id,
            source_type=source_type,
            source_key=source_key,
        )
    path = f"{SOURCE_TYPES}/{PULL_REQUEST}/watcher"

    assigned = await auth_client.post(path, json={"watcher_id": watcher["id"]}, headers=ALICE)
    listed = await auth_client.get(SOURCE_TYPES, headers=ALICE)
    unassigned = await auth_client.delete(path, headers=ALICE)
    for_bob = await auth_client.get(SOURCE_TYPES, headers=BOB)

    pull_requests = {
        "object": "source_type",
        "source_type": PULL_REQUEST,
        "watcher_id": watcher["id"],
        "watcher_name": "PR watcher",
        "subscriptions": 2,
    }
    assert assigned.json() == pull_requests
    assert listed.json() == {
        "object": "list",
        "data": [
            pull_requests,
            {
                "object": "source_type",
                "source_type": TICKET,
                "watcher_id": None,
                "watcher_name": None,
                "subscriptions": 1,
            },
        ],
    }
    assert unassigned.json() == {**pull_requests, "watcher_id": None, "watcher_name": None}
    assert for_bob.json()["data"] == []


async def test_only_the_user_or_their_custodian_assigns_a_source_type_to_a_watcher(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    watcher_id = (
        await auth_client.post(WATCHERS, json={"name": "PR watcher"}, headers=ALICE)
    ).json()["id"]
    listed = (await auth_client.get(WATCHERS, headers=ALICE)).json()["data"]
    fallback_id = next(row["id"] for row in listed if row["builtin"])
    placement = GlobalHubPlacement(host_id=HOST_ID, workspace="/Users/alice/globalhub")
    settings = UserSettingsStore(db_uri)
    settings.save_globalhub_placement(placement, user_id=ALICE_ID)
    custodian_labels = {ROLE_LABEL: CUSTODIAN_ROLE_VALUE}
    custodian_id = _session(db_uri, labels=custodian_labels, placement=placement)
    settings.save_globalhub_role_session("custodian", custodian_id, user_id=ALICE_ID)
    custodian = _runner_headers(db_uri, custodian_id, "custodian-runner")
    stale_custodian = _runner_headers(
        db_uri,
        _session(db_uri, labels=custodian_labels, placement=placement),
        "stale-custodian-runner",
    )
    manager = _runner_headers(
        db_uri, _session(db_uri, labels={ROLE_LABEL: MANAGER_ROLE_VALUE}), "manager-runner"
    )
    plain = _runner_headers(db_uri, _session(db_uri), "plain-runner")
    path = f"{SOURCE_TYPES}/{PULL_REQUEST}/watcher"
    assign = {"watcher_id": watcher_id}
    fallback = SqlAlchemyWatcherStore(db_uri).get_builtin(owner_user_id=ALICE_ID)
    assert fallback is not None
    watch_items = SqlAlchemyWatchItemStore(db_uri)
    watch_items.observe(
        fallback,
        [
            WatchObservation(source=PULL_REQUEST, source_key=PR, offsets={"state": "open"}),
            WatchObservation(source=TICKET, source_key="PROJ-12", offsets={"status": "Open"}),
        ],
        session_id=None,
        now=1_800_000_000,
    )

    statuses = [
        (await auth_client.post(path, json=assign, headers=stale_custodian)).status_code,
        (await auth_client.post(path, json=assign, headers=manager)).status_code,
        (await auth_client.post(path, json=assign, headers=plain)).status_code,
        (await auth_client.delete(path, headers=plain)).status_code,
        (
            await auth_client.post(path, json={"watcher_id": fallback_id}, headers=ALICE)
        ).status_code,
        (
            await auth_client.post(path, json={"watcher_id": uuid.uuid4().hex}, headers=ALICE)
        ).status_code,
        (await auth_client.post(path, json=assign, headers=BOB)).status_code,
        (
            await auth_client.post(
                f"{SOURCE_TYPES}/{SESSION_EVENT_SOURCE}/watcher", json=assign, headers=ALICE
            )
        ).status_code,
        (
            await auth_client.post(f"{SOURCE_TYPES}/github/watcher", json=assign, headers=ALICE)
        ).status_code,
    ]
    by_custodian = await auth_client.post(path, json=assign, headers=custodian)
    by_user = await auth_client.delete(path, headers=ALICE)

    assert statuses == [403, 403, 403, 403, 400, 404, 404, 400, 400]
    assert (by_custodian.status_code, by_custodian.json()["watcher_id"]) == (200, watcher_id)
    assert (by_user.status_code, by_user.json()["watcher_id"]) == (200, None)
    still_tracked = watch_items.find(
        fallback.id, [(PULL_REQUEST, PR), (TICKET, "PROJ-12")], owner_user_id=ALICE_ID
    )
    assert [item.source for item in still_tracked] == [TICKET]
