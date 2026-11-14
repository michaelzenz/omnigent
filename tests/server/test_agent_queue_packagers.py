"""Tests for queueing broker work and writing its notices (``agent_queue_packagers``)."""

from __future__ import annotations

import asyncio
import itertools
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.agent_tasks.board_stores import BoardStores, open_board_stores
from omnigent.agent_tasks.role_sessions import (
    RoleSessionStores,
    create_role_session,
    ensure_role_session,
)
from omnigent.agent_tasks.roles import BROKER_ROLE_KEY, MANAGER_ROLE_KEY, builtin_role
from omnigent.entities import AgentQueueItem, EventTag
from omnigent.server.agent_queue_dispatcher import AgentQueueDispatcher
from omnigent.server.agent_queue_packagers import (
    BrokerPackager,
    EventNoticeWriter,
    ManagerPackager,
    write_notice_click,
)
from omnigent.server.agent_task_delivery import SendResult, SessionSendDeps
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
PLACEMENT = GlobalHubPlacement(
    host_id="0f1e2d3c4b5a69788796a5b4c3d2e1f0", workspace="/Users/alice/globalhub"
)
ROUTE_PROMPT = "[System: route these events to managers]\n"
# Events made in one second list by id, so ids follow creation order.
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def sessions(db_uri: str) -> RoleSessionStores:
    agents = SqlAlchemyAgentStore(db_uri)
    for name in ("onih-pmv2", "onih-custodian"):
        agents.create(uuid.uuid4().hex, name, f"bundles/{name}.tar.gz")
    stores = RoleSessionStores(
        conversation_store=SqlAlchemyConversationStore(db_uri),
        agent_store=agents,
        user_settings_store=UserSettingsStore(db_uri),
        project_store=SqlAlchemyProjectStore(db_uri),
        permission_store=SqlAlchemyPermissionStore(db_uri),
    )
    stores.user_settings_store.save_globalhub_placement(PLACEMENT, user_id=ALICE)
    return stores


@pytest.fixture()
def board(sessions: RoleSessionStores) -> BoardStores:
    opened = open_board_stores(sessions.conversation_store)
    assert opened is not None
    return opened


def _event(board: BoardStores, owner: str = ALICE, *tags: str, **fields: Any) -> str:
    event_id = next(_ids)
    board.events.create(
        event_id,
        owner_user_id=owner,
        event_type=fields.pop("event_type", "github.pr.comment"),
        title=fields.pop("title", "Event"),
        tags=[EventTag(*tag.split(":", 1)) for tag in tags],
        **fields,
    )
    return event_id


async def _broker(sessions: RoleSessionStores) -> str:
    role = builtin_role(BROKER_ROLE_KEY)
    assert role is not None
    session, _ = await ensure_role_session(role, user_id=ALICE, stores=sessions)
    return session.id


def _manager(
    board: BoardStores,
    sessions: RoleSessionStores,
    title: str = "Storage",
    *,
    archived: bool = False,
) -> tuple[str, str]:
    role = builtin_role(MANAGER_ROLE_KEY)
    assert role is not None
    session = create_role_session(
        role, user_id=ALICE, placement=PLACEMENT, title=title, stores=sessions
    )
    if archived:
        sessions.conversation_store.update_conversation(session.id, archived=True)
    manager = board.managers.create(
        uuid.uuid4().hex, owner_user_id=ALICE, title=title, conversation_id=session.id
    )
    return manager.id, session.id


async def test_waiting_events_become_items_for_each_users_broker(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    session_id = uuid.uuid4().hex
    turns = [
        _event(board, event_type="session.turn.finished", source_session_id=session_id)
        for _ in range(2)
    ]
    comment = _event(board, ALICE, "host:h1")
    bobs = _event(board, BOB, "host:h1")
    packager = BrokerPackager(board=board, sessions=sessions)

    await packager.package(int(time.time()) + 31)
    await packager.package(int(time.time()) + 31)

    broker = sessions.user_settings_store.get_globalhub_role_session(
        BROKER_ROLE_KEY, user_id=ALICE
    )
    items = board.queue.list(owner_user_id=ALICE)
    carried = [
        sorted(
            event.id for event in board.events.list(owner_user_id=ALICE, queue_item_ids=[item.id])
        )
        for item in items
    ]
    assert {item.target_session_id for item in items} == {broker}
    assert carried == [sorted(turns), [comment]]
    # Bob chose no GlobalHub host, so his event waits.
    assert board.queue.list(owner_user_id=BOB) == []
    bobs_event = board.events.get(bobs, owner_user_id=BOB)
    assert bobs_event is not None and bobs_event.queue_item_id is None


async def test_the_notice_carries_the_items_waiting_events_in_tag_clusters(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    broker = await _broker(sessions)
    storage = _event(board, ALICE, "repo:storage")
    storage_pr = _event(board, ALICE, "repo:storage", "pr:7")
    untagged = _event(board)
    routed = _event(board)
    gone_session = uuid.uuid4().hex
    about_deleted_session = _event(
        board, event_type="session.turn.finished", source_session_id=gone_session
    )
    deletion_report = _event(board, event_type="session.deleted", source_session_id=gone_session)
    item = board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=broker,
        kind="events",
        event_ids=[storage, storage_pr, untagged, routed, about_deleted_session, deletion_report],
    )
    assert item is not None
    board.events.route_to_manager([routed], owner_user_id=ALICE, manager_id=uuid.uuid4().hex)
    writer = EventNoticeWriter(board=board, conversations=sessions.conversation_store)

    notice = json.loads(writer.write(item) or "{}")

    assert notice["prompt"] == ROUTE_PROMPT
    assert [
        (cluster["tags"], [event["id"] for event in cluster["events"]])
        for cluster in notice["clusters"]
    ] == [
        ([{"tag_type": "repo", "tag": "storage"}], [storage, storage_pr]),
        ([], [untagged, deletion_report]),
    ]
    dismissed = board.events.get(about_deleted_session, owner_user_id=ALICE)
    assert dismissed is not None and dismissed.state == "dismissed"
    for event_id in (storage, storage_pr, untagged, deletion_report):
        board.events.route_to_manager([event_id], owner_user_id=ALICE, manager_id=uuid.uuid4().hex)
    assert writer.write(item) is None


async def test_routed_events_become_items_for_their_managers_session(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    manager_id, manager_session = _manager(board, sessions)
    orphan_id, _ = _manager(board, sessions, "Former manager", archived=True)
    routed = [_event(board, manager_id=manager_id) for _ in range(2)]
    stranded = _event(board, manager_id=orphan_id)

    await ManagerPackager(board=board, conversations=sessions.conversation_store).package(
        int(time.time()) + 31
    )

    [item] = board.queue.list(owner_user_id=ALICE)
    carried = board.events.list(owner_user_id=ALICE, queue_item_ids=[item.id])
    assert (item.target_session_id, [event.id for event in carried]) == (manager_session, routed)
    # The archived session cannot take the event, so it waits.
    stranded_event = board.events.get(stranded, owner_user_id=ALICE)
    assert stranded_event is not None and stranded_event.queue_item_id is None


async def test_a_manager_notice_lists_its_events_by_id_and_ends_with_the_roster(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    manager_id, manager_session = _manager(board, sessions)
    task_id = next(_ids)
    board.tasks.create(
        task_id, owner_user_id=ALICE, title="S3 retries", goal="Fix them", manager_id=manager_id
    )
    board.tasks.create(
        next(_ids),
        owner_user_id=ALICE,
        title="Old work",
        goal="Done",
        manager_id=manager_id,
        state="archived",
    )
    live_session = sessions.conversation_store.create_conversation(title="Fix S3").id
    gone_session = uuid.uuid4().hex

    def session_event(event_type: str, session_id: str, title: str) -> str:
        return _event(
            board,
            event_type=event_type,
            manager_id=manager_id,
            source_session_id=session_id,
            payload={"session_title": title, "session_id": session_id},
        )

    comment = _event(board, manager_id=manager_id, task_id=task_id, title="Review comment")
    loose = _event(board, manager_id=manager_id, title="Loose comment")
    turn = session_event("session.turn.finished", live_session, "Fix S3")
    stale_turn = session_event("session.turn.finished", gone_session, "Old work")
    deleted = session_event("session.deleted", gone_session, "Old work")
    item = board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=manager_session,
        kind="events",
        event_ids=[comment, loose, turn, stale_turn, deleted],
    )
    assert item is not None

    notice = EventNoticeWriter(board=board, conversations=sessions.conversation_store).write(item)

    assert notice == "\n".join(
        [
            f"[System: 4 event(s) routed to 1 tasks: {task_id} — triage or act]",
            "[3 manager-routed event(s) have no task]",
            f"- [task:{task_id}] [event:{comment}] github.pr.comment: 'Review comment' (routed)",
            f"- [manager-routed; task unassigned] [event:{loose}] github.pr.comment: "
            "'Loose comment' (routed)",
            f"- [event:{turn}] session.turn.finished: Session 'Fix S3' finished a turn",
            f"  Session ID: {live_session}",
            "  Read the recent session transcript to see what was done. "
            "Update the task overview and stamp all assets.",
            f"- [event:{deleted}] session.deleted: Session 'Old work' was deleted",
            f"  Session ID: {gone_session}",
            "  The session no longer exists; the user deleted it. "
            "Its task may already be finished.",
            "",
            "[Task roster]",
            f"{task_id} — S3 retries (active)",
        ]
    )
    dismissed = board.events.get(stale_turn, owner_user_id=ALICE)
    assert dismissed is not None and dismissed.state == "dismissed"


def test_a_notice_click_is_written_for_the_custodian() -> None:
    item = AgentQueueItem(
        id=next(_ids),
        owner_user_id=ALICE,
        target_session_id=next(_ids),
        kind="notice_click",
        state="sending",
        seq=1,
        created_at=0,
        payload={
            "notice_id": "notice-1",
            "title": "Stale session cleanup",
            "action_id": "confirm_cleanup",
            "action_label": "Delete selected sessions",
            "publisher_label": "Task custodian",
            "inputs": {"delete_a": True, "delete_b": False, "reason": "x" * 600},
        },
    )

    assert write_notice_click(item) == "\n".join(
        [
            "[System notice click — notice notice-1]",
            "Title: Stale session cleanup",
            "Action clicked: confirm_cleanup (Delete selected sessions)",
            "Published by: Task custodian",
            "Inputs:",
            "  - delete_a: true",
            "  - delete_b: false",
            f'  - reason: "{"x" * 500}…"',
            "Interpret this click per the notice's intent and the Custodian manual. Read the "
            'notice itself with system_notice(action="get") if you need its full body.',
        ]
    )


async def test_a_waiting_event_reaches_the_broker_as_a_notice(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    sent: list[tuple[str, str]] = []

    async def send(session_id: str, text: str, **_: Any) -> SendResult:
        sent.append((session_id, text))
        return SendResult("sent")

    dispatcher = AgentQueueDispatcher(
        board=board,
        sessions=sessions,
        send_deps=SessionSendDeps(
            conversation_store=sessions.conversation_store,
            agent_store=sessions.agent_store,
            app_state=SimpleNamespace(),
        ),
        renderers={
            "events": EventNoticeWriter(
                board=board, conversations=sessions.conversation_store
            ).write
        },
        packagers=[BrokerPackager(board=board, sessions=sessions)],
        send=send,
        clock=lambda: time.time() + 31,
    )
    event_id = _event(board, ALICE, "host:h1")

    await asyncio.gather(*await dispatcher.run_once())

    [(session_id, text)] = sent
    assert session_id == await _broker(sessions)
    assert [entry["id"] for entry in json.loads(text)["clusters"][0]["events"]] == [event_id]
