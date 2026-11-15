"""Tests for the roles that run a GlobalHub board and the work waiting for them."""

from __future__ import annotations

import uuid

import httpx

from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
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


def _event(db_uri: str, owner: str = ALICE_ID, manager_id: str | None = None) -> str:
    event_id = uuid.uuid4().hex
    SqlAlchemyTaskEventStore(db_uri).create(
        event_id, owner_user_id=owner, event_type="note", title="A note", manager_id=manager_id
    )
    return event_id


def _click(db_uri: str, custodian: str) -> None:
    SqlAlchemyAgentQueueStore(db_uri).enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE_ID,
        target_session_id=custodian,
        kind="notice_click",
        payload={"action_id": "confirm"},
    )


async def test_the_rows_show_each_role_with_its_session_and_waiting_work(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    broker, custodian = _place_role_sessions(db_uri)
    storage_session = _session(db_uri, "Storage manager")
    storage = _manager(db_uri, "Storage", storage_session)
    sessionless = _manager(db_uri, "Docs", None)
    _event(db_uri)
    _event(db_uri)
    _event(db_uri, owner=BOB_ID)
    _event(db_uri, manager_id=storage)
    _click(db_uri, custodian)

    response = await auth_client.get("/v1/agent-tasks/board/roles", headers=ALICE)

    rows = response.json()["data"]
    assert [
        (row["role"], row["title"], row["manager_id"], row["session_id"], row["waiting"])
        for row in rows
    ] == [
        ("broker", "Task broker", None, broker, 2),
        ("custodian", "Task custodian", None, custodian, 1),
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
    storage = _manager(db_uri, "Storage", _session(db_uri, "Storage manager"))
    docs = _manager(db_uri, "Docs", None)
    unrouted = [_event(db_uri), _event(db_uri)]
    bobs = _event(db_uri, owner=BOB_ID)
    to_storage, to_docs = _event(db_uri, manager_id=storage), _event(db_uri, manager_id=docs)
    _click(db_uri, custodian)

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
            (to_docs, ALICE_ID),
        ]
        if (event := events.get(event_id, owner_user_id=owner)) is not None
    ]
    assert (dismissed, refused) == ([2, 1, 1], [404, 404])
    assert states == ["dismissed", "dismissed", "unrouted", "dismissed", "routed"]
    queue = SqlAlchemyAgentQueueStore(db_uri)
    assert queue.list(owner_user_id=ALICE_ID, target_session_id=custodian) == []
