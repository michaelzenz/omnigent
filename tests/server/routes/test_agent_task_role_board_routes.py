"""Tests for the roles that run a GlobalHub board and the work waiting for them."""

from __future__ import annotations

import itertools
import uuid

import httpx
import pytest

from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store import sqlalchemy_store as manager_tables
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
WORKSPACE = "/Users/alice/globalhub"


def _session(db_uri: str, title: str) -> str:
    agent_id = uuid.uuid4().hex
    SqlAlchemyAgentStore(db_uri).create(agent_id, f"agent-{agent_id}", "bundles/agent.tar.gz")
    conversations = SqlAlchemyConversationStore(db_uri)
    return conversations.create_conversation(
        agent_id=agent_id, title=title, host_id=HOST_ID, workspace=WORKSPACE
    ).id


def _place_role_sessions(db_uri: str) -> tuple[str, str]:
    """Place Alice's GlobalHub and record her broker and custodian sessions."""
    settings = UserSettingsStore(db_uri)
    settings.save_globalhub_placement(
        GlobalHubPlacement(host_id=HOST_ID, workspace=WORKSPACE), user_id=ALICE_ID
    )
    broker, custodian = _session(db_uri, "Task broker"), _session(db_uri, "Task custodian")
    settings.save_globalhub_role_session("broker", broker, user_id=ALICE_ID)
    settings.save_globalhub_role_session("custodian", custodian, user_id=ALICE_ID)
    return broker, custodian


def _manager(db_uri: str, title: str, conversation_id: str | None) -> str:
    manager_id = uuid.uuid4().hex
    SqlAlchemyManagerStore(db_uri).create(
        manager_id, owner_user_id=ALICE_ID, title=title, conversation_id=conversation_id
    )
    return manager_id


def _event(db_uri: str, owner: str = ALICE_ID, target_session_id: str | None = None) -> str:
    event_id = uuid.uuid4().hex
    SqlAlchemyTaskEventStore(db_uri).create(
        event_id,
        owner_user_id=owner,
        event_type="note",
        title="A note",
        target_session_id=target_session_id,
    )
    return event_id


def _custodian_item(db_uri: str, custodian: str, kind: str = "notice_click") -> None:
    payloads = {
        "notice_click": {"action_id": "confirm"},
        "scheduled_run": {"role_key": "custodian"},
    }
    SqlAlchemyAgentQueueStore(db_uri).enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE_ID,
        target_session_id=custodian,
        kind=kind,
        payload=payloads.get(kind),
    )


async def test_the_rows_show_each_role_with_its_session_and_waiting_work(
    auth_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, custodian = _place_role_sessions(db_uri)
    storage_session = _session(db_uri, "Storage manager")
    with monkeypatch.context() as clock:
        # Each manager is newer than the one before it.
        clock.setattr(manager_tables, "now_epoch", itertools.count(1_000).__next__)
        storage = _manager(db_uri, "Storage", storage_session)
        sessionless = _manager(db_uri, "Docs", None)
    _event(db_uri)
    _event(db_uri)
    _event(db_uri, owner=BOB_ID)
    _event(db_uri, target_session_id=storage_session)
    _event(db_uri, target_session_id=_session(db_uri, "Fix S3"))
    _custodian_item(db_uri, custodian)
    _custodian_item(db_uri, custodian, "scheduled_run")
    _custodian_item(db_uri, custodian, "source_types")

    response = await auth_client.get("/v1/agent-tasks/board/roles", headers=ALICE)

    rows = response.json()["data"]
    assert [
        (row["role"], row["title"], row["manager_id"], row["session_id"], row["waiting"])
        for row in rows
    ] == [
        ("broker", "Task broker", None, broker, 2),
        ("custodian", "Task custodian", None, custodian, 3),
        ("manager:default", "Storage", storage, storage_session, 1),
        ("manager:default", "Docs", sessionless, None, 0),
    ]
    assert [row["session"] and row["session"]["status"] for row in rows] == [
        "idle",
        "idle",
        "idle",
        None,
    ]


async def test_dismissing_clears_only_that_roles_waiting_work(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _broker, custodian = _place_role_sessions(db_uri)
    storage_session = _session(db_uri, "Storage manager")
    storage = _manager(db_uri, "Storage", storage_session)
    docs = _manager(db_uri, "Docs", None)
    unrouted = [_event(db_uri), _event(db_uri)]
    bobs = _event(db_uri, owner=BOB_ID)
    to_storage = _event(db_uri, target_session_id=storage_session)
    to_other_session = _event(db_uri, target_session_id=_session(db_uri, "Fix S3"))
    _custodian_item(db_uri, custodian)
    _custodian_item(db_uri, custodian, "scheduled_run")
    _custodian_item(db_uri, custodian, "source_types")

    dismissed = [
        (await auth_client.post(path, headers=ALICE)).json()["dismissed"]
        for path in (
            "/v1/agent-tasks/roles/broker/dismiss-waiting",
            f"/v1/agent-tasks/managers/{storage}/dismiss-waiting",
            "/v1/agent-tasks/roles/custodian/dismiss-waiting",
        )
    ]
    refused = [
        (await auth_client.post(path, headers=headers)).status_code
        for path, headers in (
            ("/v1/agent-tasks/roles/manager:default/dismiss-waiting", ALICE),
            (f"/v1/agent-tasks/managers/{docs}/dismiss-waiting", BOB),
        )
    ]

    events = SqlAlchemyTaskEventStore(db_uri)
    states = [
        event.state
        for event_id, owner in [
            (unrouted[0], ALICE_ID),
            (unrouted[1], ALICE_ID),
            (bobs, BOB_ID),
            (to_storage, ALICE_ID),
            (to_other_session, ALICE_ID),
        ]
        if (event := events.get(event_id, owner_user_id=owner)) is not None
    ]
    assert (dismissed, refused) == ([2, 1, 3], [404, 404])
    assert states == ["dismissed", "dismissed", "unrouted", "dismissed", "routed"]
    queue = SqlAlchemyAgentQueueStore(db_uri)
    assert queue.list(owner_user_id=ALICE_ID, target_session_id=custodian) == []


async def test_a_request_for_managers_waits_for_the_broker_until_dismissed(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    broker, _custodian = _place_role_sessions(db_uri)
    tasks = SqlAlchemyTaskStore(db_uri)

    def task(owner: str, manager_id: str | None = None) -> None:
        tasks.create(
            uuid.uuid4().hex,
            owner_user_id=owner,
            title="A task",
            manager_id=manager_id,
        )

    spawn = "/v1/agent-tasks/spawn-managers"
    nothing_to_assign = await auth_client.post(spawn, headers=ALICE)
    task(ALICE_ID)
    task(ALICE_ID, manager_id=uuid.uuid4().hex)
    task(BOB_ID)
    _event(db_uri)

    requested = await auth_client.post(spawn, headers=ALICE)
    # Bob has no GlobalHub host, so he has no broker.
    no_broker = await auth_client.post(spawn, headers=BOB)
    queue = SqlAlchemyAgentQueueStore(db_uri)
    queued = [item.kind for item in queue.list(owner_user_id=ALICE_ID, target_session_id=broker)]
    roles = await auth_client.get("/v1/agent-tasks/board/roles", headers=ALICE)
    dismissed = await auth_client.post(
        "/v1/agent-tasks/roles/broker/dismiss-waiting", headers=ALICE
    )

    assert [
        (response.json()["unmanaged_count"], response.json()["queued"])
        for response in (nothing_to_assign, requested)
    ] == [(0, False), (1, True)]
    assert no_broker.status_code == 400
    assert queued == ["spawn_managers"]
    # The broker waits on the unrouted event and the request.
    assert (roles.json()["data"][0]["waiting"], dismissed.json()["dismissed"]) == (2, 2)
    assert queue.list(owner_user_id=ALICE_ID, target_session_id=broker) == []
