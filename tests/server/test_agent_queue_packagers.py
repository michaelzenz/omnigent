"""Tests for queueing GlobalHub work and writing its notices (``agent_queue_packagers``)."""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.agent_tasks.board_stores import BoardStores, open_board_stores
from omnigent.agent_tasks.default_watchers import ensure_fallback_watcher
from omnigent.agent_tasks.role_schedules import next_run_at
from omnigent.agent_tasks.role_sessions import (
    RoleSessionStores,
    create_role_session,
    ensure_role_session,
)
from omnigent.agent_tasks.roles import (
    BROKER_ROLE_KEY,
    CUSTODIAN_ROLE_KEY,
    MANAGER_ROLE_KEY,
    builtin_role,
)
from omnigent.db.db_models import workspace_scope
from omnigent.entities import SESSION_EVENT_SOURCE, AgentQueueItem, EventTag
from omnigent.runtime.prompt import EVENT_NOTICE_SHAPE
from omnigent.server import agent_queue_packagers
from omnigent.server.agent_queue_dispatcher import AgentQueueDispatcher
from omnigent.server.agent_queue_packagers import (
    BrokerPackager,
    EventNoticeWriter,
    RoleSchedulePackager,
    ScheduledRunWriter,
    SourceTypesWriter,
    SpawnManagersWriter,
    SubscriberPackager,
    write_notice_click,
)
from omnigent.server.agent_task_delivery import SendResult, SessionSendDeps
from omnigent.server.routes._sessions.helpers import SessionLiveness
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from omnigent.stores.subscription_store import sqlalchemy_store as subscription_tables
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore
from omnigent.stores.watch_item_store import (
    WATCH_DONE_EVENT_TYPE,
    WatchFinish,
    WatchObservation,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"
PLACEMENT = GlobalHubPlacement(
    host_id="0f1e2d3c4b5a69788796a5b4c3d2e1f0", workspace="/Users/alice/globalhub"
)
ROUTE_PROMPT = "[System: route these events to tasks or managers]\n"
PULL_REQUEST = "github.pull_request"
TICKET = "jira.issue"
SLACK_CONVERSATION = "slack.conversation"
SLACK_DM = "D0000000001"
SLACK_CHANNEL = "C0000000001"
PR = "https://github.com/example-org/repo/pull/12"
# Events made in one second list by id, so ids follow creation order.
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def sessions(db_uri: str) -> RoleSessionStores:
    agents = SqlAlchemyAgentStore(db_uri)
    for name in ("onih-pmv2", "onih-custodian", "onih-watcher"):
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


@pytest.fixture()
def send_deps(sessions: RoleSessionStores) -> SessionSendDeps:
    return SessionSendDeps(
        conversation_store=sessions.conversation_store,
        agent_store=sessions.agent_store,
        app_state=SimpleNamespace(),
    )


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


def _follow(
    board: BoardStores,
    session_id: str,
    source_type: str,
    source_key: str,
    note: str | None = None,
) -> None:
    board.subscriptions.subscribe(
        owner_user_id=ALICE,
        session_id=session_id,
        source_type=source_type,
        source_key=source_key,
        note=note,
    )


def _queue_item(kind: str, payload: dict[str, Any]) -> AgentQueueItem:
    return AgentQueueItem(
        id=next(_ids),
        owner_user_id=ALICE,
        target_session_id=next(_ids),
        kind=kind,
        state="sending",
        seq=1,
        created_at=0,
        payload=payload,
    )


def _state(board: BoardStores, event_id: str) -> str | None:
    event = board.events.get(event_id, owner_user_id=ALICE)
    return event.state if event is not None else None


async def test_waiting_events_become_items_for_each_users_broker(
    board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
) -> None:
    session_id = uuid.uuid4().hex
    turns = [
        _event(board, event_type="session.turn.finished", source_session_id=session_id)
        for _ in range(2)
    ]
    comment = _event(board, ALICE, "host:h1")
    bobs = _event(board, BOB, "host:h1")
    packager = BrokerPackager(board=board, sessions=sessions, send_deps=send_deps)

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


async def test_events_in_another_workspace_are_queued_in_that_workspace(
    board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
) -> None:
    with workspace_scope(7):
        for name in ("onih-pmv2", "onih-custodian"):
            sessions.agent_store.create(uuid.uuid4().hex, name, f"bundles/{name}.tar.gz")
        sessions.user_settings_store.save_globalhub_placement(PLACEMENT, user_id=ALICE)
        comment = _event(board, ALICE, "host:h1")
    packager = BrokerPackager(board=board, sessions=sessions, send_deps=send_deps)

    # The packager runs outside any workspace, as it does in the server.
    await packager.package(int(time.time()) + 31)

    assert board.queue.list(owner_user_id=ALICE) == []
    with workspace_scope(7):
        broker = sessions.user_settings_store.get_globalhub_role_session(
            BROKER_ROLE_KEY, user_id=ALICE
        )
        items = board.queue.list(owner_user_id=ALICE)
        carried = [
            event.id
            for item in items
            for event in board.events.list(owner_user_id=ALICE, queue_item_ids=[item.id])
        ]
    assert ([item.target_session_id for item in items], carried) == ([broker], [comment])


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
    board.events.route_to_session([routed], owner_user_id=ALICE, session_id=uuid.uuid4().hex)
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
        board.events.route_to_session([event_id], owner_user_id=ALICE, session_id=uuid.uuid4().hex)
    assert writer.write(item) is None


async def test_the_notice_lists_the_newest_followers_of_a_broker_routed_source(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    broker = await _broker(sessions)
    board.subscriptions.set_routing(
        owner_user_id=ALICE, source_type=SLACK_CONVERSATION, routing="broker"
    )
    worker_session = sessions.conversation_store.create_conversation(title="Fix S3").id
    task = board.tasks.create(next(_ids), owner_user_id=ALICE, title="S3 retries")
    board.workers.create(
        next(_ids), owner_user_id=ALICE, task_id=task.id, session_id=worker_session
    )
    _follow(board, worker_session, SLACK_CONVERSATION, SLACK_DM, note="Waits for the design reply")
    for _ in range(21):
        _follow(board, uuid.uuid4().hex, SLACK_CONVERSATION, SLACK_CHANNEL)
    _follow(board, uuid.uuid4().hex, PULL_REQUEST, PR)
    direct = _event(board, source=SLACK_CONVERSATION, source_key=SLACK_DM)
    channel = _event(board, source=SLACK_CONVERSATION, source_key=SLACK_CHANNEL)
    pull_request = _event(board, source=PULL_REQUEST, source_key=PR)
    item = board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=broker,
        kind="events",
        event_ids=[direct, channel, pull_request],
    )
    assert item is not None

    writer = EventNoticeWriter(board=board, conversations=sessions.conversation_store)
    notice = json.loads(writer.write(item) or "{}")

    entries = {entry["id"]: entry for cluster in notice["clusters"] for entry in cluster["events"]}
    assert entries[direct]["followed_by"] == [
        {
            "session_id": worker_session,
            "title": "Fix S3",
            "note": "Waits for the design reply",
            "tasks": [{"id": task.id, "title": "S3 retries"}],
        }
    ]
    assert "more_followers" not in entries[direct]
    assert (len(entries[channel]["followed_by"]), entries[channel]["more_followers"]) == (20, 1)
    assert "followed_by" not in entries[pull_request]


async def test_routed_events_become_items_for_the_managers_session_they_went_to(
    board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
) -> None:
    _, manager_session = _manager(board, sessions)
    _, archived_session = _manager(board, sessions, "Former manager", archived=True)
    _, deleted_session = _manager(board, sessions, "Manager without a session")
    await sessions.conversation_store.delete_conversation(deleted_session)
    routed = [_event(board, target_session_id=manager_session) for _ in range(2)]
    stranded = [
        _event(board, target_session_id=archived_session),
        _event(board, target_session_id=deleted_session),
    ]
    packager = SubscriberPackager(board=board, sessions=sessions, send_deps=send_deps)

    await packager.package(int(time.time()) + 31)

    [item] = board.queue.list(owner_user_id=ALICE)
    carried = board.events.list(owner_user_id=ALICE, queue_item_ids=[item.id])
    assert (item.target_session_id, [event.id for event in carried]) == (manager_session, routed)
    waiting = [board.events.get(event_id, owner_user_id=ALICE) for event_id in stranded]
    assert [(event.state, event.queue_item_id) for event in waiting if event] == [
        ("routed", None),
        ("routed", None),
    ]


async def test_another_session_gets_each_event_once_and_a_gone_one_stops_following(
    board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
) -> None:
    conversations = sessions.conversation_store
    follower = conversations.create_conversation(title="Fix S3").id
    archived = conversations.create_conversation(title="Old work").id
    conversations.update_conversation(archived, archived=True)
    gone = uuid.uuid4().hex
    for session_id in (archived, gone):
        _follow(board, session_id, PULL_REQUEST, PR)
    delivered = _event(board, target_session_id=follower)
    sent = board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=follower,
        kind="events",
        event_ids=[delivered],
    )
    assert sent is not None
    [claimed] = board.queue.claim_due_all_workspaces(now=int(time.time()))
    board.queue.mark_sent(claimed.id, owner_user_id=ALICE)
    board.queue.mark_done(claimed.id, owner_user_id=ALICE)
    fresh = _event(board, target_session_id=follower)
    stranded = [_event(board, target_session_id=archived), _event(board, target_session_id=gone)]
    packager = SubscriberPackager(board=board, sessions=sessions, send_deps=send_deps)

    await packager.package(int(time.time()) + 31)

    [item] = board.queue.list(owner_user_id=ALICE, states=["queued"])
    carried = board.events.list(owner_user_id=ALICE, queue_item_ids=[item.id])
    assert (item.target_session_id, [event.id for event in carried]) == (follower, [fresh])
    assert [_state(board, event_id) for event_id in (delivered, *stranded)] == ["dismissed"] * 3
    assert [
        board.subscriptions.list_for_session(session_id, owner_user_id=ALICE)
        for session_id in (archived, gone)
    ] == [[], []]


async def test_a_broker_whose_turn_lost_its_runner_gets_its_waiting_events(
    board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
) -> None:
    broker = await _broker(sessions)
    assert sessions.conversation_store.set_runner_id(broker, f"runner_{broker}")
    sessions.conversation_store.set_session_live_status(broker, "running")
    gone = SessionLiveness(runner_online=False, host_online=True)
    deps = dataclasses.replace(send_deps, liveness_lookup=lambda ids: dict.fromkeys(ids, gone))
    comment = _event(board)
    packager = BrokerPackager(board=board, sessions=sessions, send_deps=deps)

    await packager.package(int(time.time()) + 31)

    [item] = board.queue.list(owner_user_id=ALICE)
    carried = board.events.list(owner_user_id=ALICE, queue_item_ids=[item.id])
    assert (item.target_session_id, [event.id for event in carried]) == (broker, [comment])


async def test_a_manager_notice_lists_its_events_by_id_with_their_source_and_reporter(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    _, manager_session = _manager(board, sessions)
    live_session = sessions.conversation_store.create_conversation(title="Fix S3").id
    gone_session = uuid.uuid4().hex

    def session_event(event_type: str, session_id: str, title: str) -> str:
        return _event(
            board,
            event_type=event_type,
            target_session_id=manager_session,
            source=SESSION_EVENT_SOURCE,
            source_key=session_id,
            source_session_id=session_id,
            payload={"session_title": title, "session_id": session_id},
        )

    watched = _event(
        board,
        target_session_id=manager_session,
        title="Review comment",
        source=PULL_REQUEST,
        source_key=PR,
        payload={"watcher": {"id": "w1", "name": "PR watcher"}},
    )
    reported = _event(
        board,
        target_session_id=manager_session,
        title="CI failed",
        source=PULL_REQUEST,
        source_key=PR,
        source_session_id=live_session,
    )
    loose = _event(board, target_session_id=manager_session, title="Loose comment")
    turn = session_event("session.turn.finished", live_session, "Fix S3")
    stale_turn = session_event("session.turn.finished", gone_session, "Old work")
    deleted = session_event("session.deleted", gone_session, "Old work")
    item = board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=manager_session,
        kind="events",
        event_ids=[watched, reported, loose, turn, stale_turn, deleted],
    )
    assert item is not None

    notice = EventNoticeWriter(board=board, conversations=sessions.conversation_store).write(item)

    assert notice == "\n".join(
        [
            "[System: 5 event(s) for you — triage or act]",
            f"- [event:{watched}] github.pr.comment {PULL_REQUEST} {PR}: 'Review comment' "
            "(reported by the watcher 'PR watcher')",
            f"- [event:{reported}] github.pr.comment {PULL_REQUEST} {PR}: 'CI failed' "
            f"(reported by session {live_session})",
            f"- [event:{loose}] github.pr.comment: 'Loose comment'",
            f"- [event:{turn}] session.turn.finished: Session 'Fix S3' finished a turn",
            f"  Session ID: {live_session}",
            "  Read the recent session transcript to see what was done. "
            "Update the task overview, stamp all assets, then assign the event to its task.",
            f"- [event:{deleted}] session.deleted: Session 'Old work' was deleted",
            f"  Session ID: {gone_session}",
            "  The session no longer exists; the user deleted it. "
            "Its task may already be finished.",
        ]
    )
    assert _state(board, stale_turn) == "dismissed"


async def test_a_manager_notice_names_the_task_that_the_broker_found_for_an_event(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    manager_id, manager_session = _manager(board, sessions)
    task = board.tasks.create(
        next(_ids), owner_user_id=ALICE, title="Fix S3 retries", manager_id=manager_id
    )
    live_session = sessions.conversation_store.create_conversation(title="Fix S3").id
    matched = _event(
        board,
        target_session_id=manager_session,
        title="Review comment",
        source=PULL_REQUEST,
        source_key=PR,
        task_id=task.id,
    )
    turn = _event(
        board,
        event_type="session.turn.finished",
        target_session_id=manager_session,
        source=SESSION_EVENT_SOURCE,
        source_key=live_session,
        source_session_id=live_session,
        payload={"session_title": "Fix S3", "session_id": live_session},
        task_id=task.id,
    )
    loose = _event(board, target_session_id=manager_session, title="Loose comment")
    item = board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=manager_session,
        kind="events",
        event_ids=[matched, turn, loose],
    )
    assert item is not None

    notice = EventNoticeWriter(board=board, conversations=sessions.conversation_store).write(item)

    match_line = f"  Broker match: task {task.id} — Fix S3 retries (active)"
    assert notice == "\n".join(
        [
            "[System: 3 event(s) for you — triage or act]",
            f"- [event:{matched}] github.pr.comment {PULL_REQUEST} {PR}: 'Review comment'",
            match_line,
            f"- [event:{loose}] github.pr.comment: 'Loose comment'",
            f"- [event:{turn}] session.turn.finished: Session 'Fix S3' finished a turn",
            f"  Session ID: {live_session}",
            "  Read the recent session transcript to see what was done. "
            "Update the task overview, stamp all assets, then assign the event to its task.",
            match_line,
        ]
    )


async def test_another_session_gets_one_event_notice_for_the_sources_it_follows(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    conversations = sessions.conversation_store
    follower = conversations.create_conversation(title="Fix S3").id
    worker = conversations.create_conversation(title="Review PR 12").id
    comment = _event(
        board,
        target_session_id=follower,
        title="alice commented: please add a test",
        source=PULL_REQUEST,
        source_key=PR,
        payload={"watcher": {"id": "w1", "name": "PR watcher"}},
    )
    turn = _event(
        board,
        event_type="session.turn.finished",
        title="Session turn finished: Review PR 12",
        target_session_id=follower,
        source=SESSION_EVENT_SOURCE,
        source_key=worker,
        source_session_id=worker,
        payload={"session_title": "Review PR 12", "session_id": worker},
    )
    item = board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=follower,
        kind="events",
        event_ids=[comment, turn],
    )
    assert item is not None

    notice = EventNoticeWriter(board=board, conversations=conversations).write(item)

    assert notice == "\n".join(
        [
            EVENT_NOTICE_SHAPE.replace("<N>", "2"),
            f"- github.pr.comment {PULL_REQUEST} {PR}: 'alice commented: please add a test' "
            "(reported by the watcher 'PR watcher')",
            f"- Session turn finished: Review PR 12 (session 'Review PR 12', id {worker})",
        ]
    )


async def test_the_broker_gets_an_event_notice_for_a_source_it_follows(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    broker = await _broker(sessions)
    comment = _event(
        board,
        target_session_id=broker,
        title="alice commented: please add a test",
        source=PULL_REQUEST,
        source_key=PR,
    )
    item = board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=broker,
        kind="events",
        event_ids=[comment],
    )
    assert item is not None

    notice = EventNoticeWriter(board=board, conversations=sessions.conversation_store).write(item)

    assert notice == "\n".join(
        [
            EVENT_NOTICE_SHAPE.replace("<N>", "1"),
            f"- github.pr.comment {PULL_REQUEST} {PR}: 'alice commented: please add a test'",
        ]
    )


async def test_a_due_cleanup_queues_one_message_for_the_custodian_and_skips_missed_runs(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    now = 1_800_000_000
    daily = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    schedules = board.role_schedules
    schedules.save(
        CUSTODIAN_ROLE_KEY, owner_user_id=ALICE, rrule=daily, timezone="UTC", next_run_at=now - 60
    )
    # Bob's server missed three days, and he has no GlobalHub host.
    schedules.save(
        CUSTODIAN_ROLE_KEY,
        owner_user_id=BOB,
        rrule=daily,
        timezone="UTC",
        next_run_at=now - 3 * 86_400,
    )
    packager = RoleSchedulePackager(board=board, sessions=sessions)

    await packager.package(now)
    following = next_run_at(daily, "UTC", after=now, anchor=now)
    moved = [schedules.get(CUSTODIAN_ROLE_KEY, owner_user_id=owner) for owner in (ALICE, BOB)]
    # The next day's cleanup finds the first one still waiting for the custodian.
    await packager.package(following)

    custodian = sessions.user_settings_store.get_globalhub_role_session(
        CUSTODIAN_ROLE_KEY, user_id=ALICE
    )
    items = board.queue.list(owner_user_id=ALICE)
    assert [each.next_run_at if each else None for each in moved] == [following, following]
    assert [(item.kind, item.target_session_id, item.payload) for item in items] == [
        ("scheduled_run", custodian, {"role_key": CUSTODIAN_ROLE_KEY})
    ]
    written = ScheduledRunWriter(board=board).write(items[0])
    assert (written or "").startswith("[System: session cleanup]\n")
    assert board.queue.list(owner_user_id=BOB) == []


async def test_the_custodian_and_a_watcher_due_together_each_get_one_run_in_their_session(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    now = 1_800_000_000
    watcher = board.watchers.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    for role_key in (CUSTODIAN_ROLE_KEY, watcher.role_key):
        board.role_schedules.save(
            role_key,
            owner_user_id=ALICE,
            rrule="FREQ=HOURLY;BYMINUTE=0",
            timezone="UTC",
            next_run_at=now - 60,
        )
    packager = RoleSchedulePackager(board=board, sessions=sessions)

    await packager.package(now)
    # A pass an hour later finds both runs still waiting, so it adds none.
    await packager.package(now + 3_600)

    custodian = sessions.user_settings_store.get_globalhub_role_session(
        CUSTODIAN_ROLE_KEY, user_id=ALICE
    )
    recorded = board.watchers.get(watcher.id, owner_user_id=ALICE)
    assert recorded is not None and recorded.conversation_id is not None
    items = board.queue.list(owner_user_id=ALICE)
    assert sorted((item.payload["role_key"], item.target_session_id) for item in items) == sorted(
        [(CUSTODIAN_ROLE_KEY, custodian), (watcher.role_key, recorded.conversation_id)]
    )
    watcher_session = sessions.conversation_store.get_conversation(recorded.conversation_id)
    assert watcher_session is not None and watcher_session.title == "PR watcher"
    writer = ScheduledRunWriter(board=board)
    texts = {item.payload["role_key"]: writer.write(item) for item in items}
    assert (texts[watcher.role_key] or "").startswith("[System: scheduled watcher run]\n")
    no_items = "[Tracked items] (the least recently checked first)\n(none yet)"
    assert (texts[watcher.role_key] or "").endswith(no_items)
    assert (texts[CUSTODIAN_ROLE_KEY] or "").startswith("[System: session cleanup]\n")
    # A run queued for a watcher that was deleted has nothing left to say.
    board.watchers.delete(watcher.id, owner_user_id=ALICE)
    watcher_item = next(item for item in items if item.payload["role_key"] == watcher.role_key)
    assert writer.write(watcher_item) is None


async def test_a_watcher_run_lists_its_items_and_drops_items_done_long_ago(
    board: BoardStores, sessions: RoleSessionStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1_800_000_000
    day = 86_400
    watcher = board.watchers.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    for n in range(3):
        seen = WatchObservation(
            source=PULL_REQUEST,
            source_key=f"https://example.com/pr/{n}",
            offsets={"state": "open"},
            title=f"PR {n}",
        )
        # The first 40 days ago; the others 13 days ago, an hour apart, so the run lists them
        # in this order.
        at = now - 40 * day if n == 0 else now - 13 * day + n * 3_600
        board.watch_items.observe(watcher, [seen], session_id=None, now=at)
    board.watch_items.finish(
        watcher,
        [WatchFinish(source=PULL_REQUEST, source_key="https://example.com/pr/0")],
        session_id=None,
        now=now - 31 * day,
    )
    board.role_schedules.save(
        watcher.role_key, owner_user_id=ALICE, rrule="FREQ=HOURLY", timezone="UTC", next_run_at=now
    )

    await RoleSchedulePackager(board=board, sessions=sessions).package(now)

    (item,) = board.queue.list(owner_user_id=ALICE)
    text = ScheduledRunWriter(board=board).write(item) or ""
    monkeypatch.setattr(agent_queue_packagers, "_RUN_ITEMS_SHOWN", 1)
    short = ScheduledRunWriter(board=board).write(item) or ""
    everything = board.watch_items.count(
        watcher.id, owner_user_id=ALICE, statuses=("active", "done")
    )
    assert everything == 2
    pr_lines = [
        f'- {PULL_REQUEST} https://example.com/pr/{n} "PR {n}": state=open '
        f"(checked 2027-01-02 {hour} UTC)"
        for n, hour in ((1, "09:00"), (2, "10:00"))
    ]
    assert text.splitlines()[-3:] == [
        "[Tracked items] (the least recently checked first)",
        *pr_lines,
    ]
    assert short.splitlines()[-2:] == [
        pr_lines[0],
        '… and 1 more; page through them with watch_items action "list".',
    ]


def test_a_watcher_run_lists_the_followed_sources_new_to_it_and_done_ones_due_a_check(
    board: BoardStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1_800_000_000
    day = 86_400
    monkeypatch.setattr(subscription_tables, "now_epoch", itertools.count(now - day).__next__)
    monkeypatch.setattr(agent_queue_packagers, "now_epoch", lambda: now)
    watcher = board.watchers.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    ticket_watcher = board.watchers.create(
        uuid.uuid4().hex, owner_user_id=ALICE, name="Ticket watcher"
    )
    subscriptions = board.subscriptions
    subscriptions.assign_type(owner_user_id=ALICE, source_type=PULL_REQUEST, watcher_id=watcher.id)
    subscriptions.assign_type(
        owner_user_id=ALICE, source_type=TICKET, watcher_id=ticket_watcher.id
    )
    untracked, active, recently_done, done_long_ago = (
        f"https://github.com/example-org/repo/pull/{n}" for n in range(1, 5)
    )
    for source_key in (untracked, active, recently_done, done_long_ago):
        _follow(board, uuid.uuid4().hex, PULL_REQUEST, source_key)
    _follow(board, uuid.uuid4().hex, TICKET, "PROJ-12")
    private = "Private repository: read it with the GitHub MCP server of the work account."
    _follow(board, uuid.uuid4().hex, PULL_REQUEST, untracked, note="Opened by\n  this session")
    _follow(board, uuid.uuid4().hex, PULL_REQUEST, untracked, note="Reviewing it for the release")
    _follow(board, uuid.uuid4().hex, PULL_REQUEST, active, note=private)

    def observe(source_key: str, at: int) -> None:
        seen = WatchObservation(
            source=PULL_REQUEST, source_key=source_key, offsets={"state": "open"}
        )
        board.watch_items.observe(watcher, [seen], session_id=None, now=at)

    def finish(source_key: str, at: int) -> None:
        done = WatchFinish(source=PULL_REQUEST, source_key=source_key)
        board.watch_items.finish(watcher, [done], session_id=None, now=at)

    observe(active, now - 3_600)
    observe(recently_done, now - 3_600)
    finish(recently_done, now - 1_800)
    observe(done_long_ago, now - 2 * day)
    finish(done_long_ago, now - 2 * day)

    text = ScheduledRunWriter(board=board).write(
        _queue_item("scheduled_run", {"role_key": watcher.role_key})
    )

    assert (text or "").split("\n\n")[1:] == [
        "\n".join(
            [
                "[Tracked items] (the least recently checked first)",
                f"- {PULL_REQUEST} {active}: state=open (checked 2027-01-15 07:00 UTC)",
                f"  Note: {private}",
            ]
        ),
        "\n".join(
            [
                "[Followed sources] (sessions follow these)",
                f"- {PULL_REQUEST} {untracked}: not tracked yet; observe it with this source "
                "type to start",
                "  Note: Opened by this session",
                f"- {PULL_REQUEST} {done_long_ago}: done since 2027-01-13; observe it to see "
                "whether it reopened",
            ]
        ),
    ]


def test_a_watcher_run_shows_the_cursor_that_the_watcher_saved(board: BoardStores) -> None:
    watcher = board.watchers.create(uuid.uuid4().hex, owner_user_id=ALICE, name="Slack watcher")
    writer = ScheduledRunWriter(board=board)
    run = _queue_item("scheduled_run", {"role_key": watcher.role_key})
    before = writer.write(run)
    board.watchers.set_cursor(watcher.id, "1700000000.000100", owner_user_id=ALICE)

    after = writer.write(run)

    no_items = "[Tracked items] (the least recently checked first)\n(none yet)"
    assert (before or "").split("\n\n")[1:] == [no_items]
    assert (after or "").split("\n\n")[1:] == ["[Cursor] 1700000000.000100", no_items]


def test_the_fallback_watcher_run_checks_the_followed_sources_that_no_watcher_covers(
    board: BoardStores,
) -> None:
    fallback = ensure_fallback_watcher(
        board.watchers, board.role_schedules, owner_user_id=ALICE, now=1_800_000_000
    )
    run = _queue_item("scheduled_run", {"role_key": fallback.role_key})
    writer = ScheduledRunWriter(board=board)
    without_sources = writer.write(run)
    watcher = board.watchers.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    board.subscriptions.assign_type(
        owner_user_id=ALICE, source_type=PULL_REQUEST, watcher_id=watcher.id
    )
    _follow(board, uuid.uuid4().hex, PULL_REQUEST, PR)
    _follow(board, uuid.uuid4().hex, SESSION_EVENT_SOURCE, uuid.uuid4().hex)
    covered_or_own = writer.write(run)
    _follow(board, uuid.uuid4().hex, TICKET, "PROJ-12")

    text = writer.write(run)

    assert (without_sources, covered_or_own) == (None, None)
    assert (text or "").split("\n\n")[-1] == "\n".join(
        [
            "[Followed sources] (sessions follow these)",
            f"- {TICKET} PROJ-12: not tracked yet; observe it with this source type to start",
        ]
    )


async def test_the_fallback_watcher_runs_only_while_a_followed_source_has_no_watcher(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    now = 1_800_000_000
    fallback = ensure_fallback_watcher(
        board.watchers, board.role_schedules, owner_user_id=ALICE, now=now
    )
    board.role_schedules.save(
        fallback.role_key,
        owner_user_id=ALICE,
        rrule="FREQ=HOURLY;BYMINUTE=0",
        timezone="UTC",
        next_run_at=now,
    )
    packager = RoleSchedulePackager(board=board, sessions=sessions)

    await packager.package(now)
    without_sources = board.queue.list(owner_user_id=ALICE)
    _follow(board, uuid.uuid4().hex, TICKET, "PROJ-12")
    await packager.package(now + 3_600)

    recorded = board.watchers.get(fallback.id, owner_user_id=ALICE)
    assert without_sources == []
    assert recorded is not None and recorded.conversation_id is not None
    assert [
        (item.payload, item.target_session_id) for item in board.queue.list(owner_user_id=ALICE)
    ] == [({"role_key": fallback.role_key}, recorded.conversation_id)]


async def test_a_watcher_run_ends_the_subscriptions_to_sources_done_for_a_week(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    day = 86_400
    now = int(time.time()) + 30 * day
    watcher = board.watchers.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    follower = uuid.uuid4().hex
    done_for_a_week, done_lately = PR, "https://github.com/example-org/repo/pull/13"
    followed_after_done = "https://github.com/example-org/repo/pull/14"
    for source_key, done_at in (
        (done_for_a_week, now - 8 * day),
        (done_lately, now - 6 * day),
        (followed_after_done, now - 31 * day),
    ):
        _follow(board, follower, PULL_REQUEST, source_key)
        seen = WatchObservation(
            source=PULL_REQUEST, source_key=source_key, offsets={"state": "open"}
        )
        board.watch_items.observe(watcher, [seen], session_id=None, now=done_at)
        done = WatchFinish(source=PULL_REQUEST, source_key=source_key)
        board.watch_items.finish(watcher, [done], session_id=None, now=done_at)
    board.role_schedules.save(
        watcher.role_key, owner_user_id=ALICE, rrule="FREQ=HOURLY", timezone="UTC", next_run_at=now
    )

    await RoleSchedulePackager(board=board, sessions=sessions).package(now)

    followed = board.subscriptions.list_for_session(follower, owner_user_id=ALICE)
    assert sorted(subscription.source_key for subscription in followed) == [
        done_lately,
        followed_after_done,
    ]


async def test_a_watcher_run_finishes_the_items_that_did_not_change_for_two_weeks(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    day = 86_400
    now = int(time.time()) + 30 * day
    watcher = board.watchers.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    follower = uuid.uuid4().hex
    quiet_followed, quiet, changed_lately, new = (
        f"https://github.com/example-org/repo/pull/{n}" for n in range(1, 5)
    )
    _follow(board, follower, PULL_REQUEST, quiet_followed)

    def observe(source_key: str, at: int, state: str = "open") -> None:
        seen = WatchObservation(
            source=PULL_REQUEST,
            source_key=source_key,
            offsets={"state": state},
            title=f"PR {source_key[-1]}",
        )
        board.watch_items.observe(watcher, [seen], session_id=None, now=at)

    observe(quiet_followed, now - 15 * day)
    observe(quiet, now - 20 * day)
    observe(changed_lately, now - 20 * day)
    observe(changed_lately, now - day, state="closed")
    observe(new, now - day)
    board.role_schedules.save(
        watcher.role_key, owner_user_id=ALICE, rrule="FREQ=HOURLY", timezone="UTC", next_run_at=now
    )

    await RoleSchedulePackager(board=board, sessions=sessions).package(now)

    keys = [(PULL_REQUEST, key) for key in (quiet_followed, quiet, changed_lately, new)]
    found = board.watch_items.find(watcher.id, keys, owner_user_id=ALICE)
    assert {item.source_key: item.status for item in found} == {
        quiet_followed: "done",
        quiet: "done",
        changed_lately: "active",
        new: "active",
    }
    done = [
        (event.target_session_id, event.title)
        for event in board.events.list(owner_user_id=ALICE)
        if event.event_type == WATCH_DONE_EVENT_TYPE
    ]
    assert done == [(follower, "Done: PR 1: No activity for 14 days")]


def test_the_custodian_hears_of_the_source_types_without_a_watcher_or_a_routing(
    board: BoardStores,
) -> None:
    run = _queue_item("scheduled_run", {"role_key": CUSTODIAN_ROLE_KEY})
    request = _queue_item("source_types", {})
    run_writer, types_writer = ScheduledRunWriter(board=board), SourceTypesWriter(board=board)
    plain_run, nothing_pending = run_writer.write(run), types_writer.write(request)
    ensure_fallback_watcher(
        board.watchers, board.role_schedules, owner_user_id=ALICE, now=1_800_000_000
    )
    ticket_watcher = board.watchers.create(
        uuid.uuid4().hex, owner_user_id=ALICE, name="Ticket watcher", description="Jira tickets"
    )
    board.subscriptions.assign_type(
        owner_user_id=ALICE, source_type=TICKET, watcher_id=ticket_watcher.id
    )
    board.subscriptions.set_routing(owner_user_id=ALICE, source_type=TICKET, routing="followers")
    for source_type, source_key in (
        (PULL_REQUEST, PR),
        (PULL_REQUEST, PR),
        (TICKET, "PROJ-12"),
        (SESSION_EVENT_SOURCE, uuid.uuid4().hex),
    ):
        _follow(board, uuid.uuid4().hex, source_type, source_key)

    pending = types_writer.write(request)
    run_text = run_writer.write(run)

    assert (plain_run or "").startswith("[System: session cleanup]\n")
    assert nothing_pending is None
    assert pending == "\n".join(
        [
            "[System: assign source types to watchers]",
            "Sessions follow sources of the types below, and no watcher checks them yet. Follow "
            '"Assigning source types" in the Custodian manual: assign each type to the watcher '
            "whose manual covers it with source_watchers, and leave a type unassigned when no "
            "manual covers it.",
            "Types without a watcher:",
            f"- {PULL_REQUEST}: 2 subscription(s), e.g. {PR}",
            "Watchers (read a manual with pmv2_read path /v1/agent-tasks/watchers/<id>):",
            f"- {ticket_watcher.id} Ticket watcher: Jira tickets",
            'Types without a routing. Follow "Assigning source types" in the Custodian '
            'manual: set each type with source_watchers action "route", to "broker" for a '
            "stream in which one source carries many topics, e.g. a direct message, a "
            'channel or an inbox, else to "followers":',
            f"- {PULL_REQUEST}, e.g. {PR}",
        ]
    )
    assert run_text == f"{plain_run}\n\n{pending}"


async def test_the_schedule_of_a_deleted_watcher_goes_away_without_a_run(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    now = 1_800_000_000
    role_key = f"watcher:{uuid.uuid4().hex}"
    board.role_schedules.save(
        role_key, owner_user_id=ALICE, rrule="FREQ=HOURLY", timezone="UTC", next_run_at=now - 60
    )

    await RoleSchedulePackager(board=board, sessions=sessions).package(now)

    assert board.role_schedules.get(role_key, owner_user_id=ALICE) is None
    assert board.queue.list(owner_user_id=ALICE) == []


async def test_a_waiting_run_of_another_role_does_not_hold_back_a_due_run(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    now = 1_800_000_000
    role = builtin_role(CUSTODIAN_ROLE_KEY)
    assert role is not None
    custodian, _ = await ensure_role_session(role, user_id=ALICE, stores=sessions)
    # Only the role's own waiting run holds back its next one, even in a shared session.
    board.queue.enqueue(
        next(_ids),
        owner_user_id=ALICE,
        target_session_id=custodian.id,
        kind="scheduled_run",
        payload={"role_key": "watcher:docs"},
    )
    board.role_schedules.save(
        CUSTODIAN_ROLE_KEY,
        owner_user_id=ALICE,
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        timezone="UTC",
        next_run_at=now - 60,
    )

    await RoleSchedulePackager(board=board, sessions=sessions).package(now)

    assert [item.payload["role_key"] for item in board.queue.list(owner_user_id=ALICE)] == [
        "watcher:docs",
        CUSTODIAN_ROLE_KEY,
    ]


async def test_a_schedule_without_a_next_run_sends_the_due_run_and_turns_off(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    now = 1_800_000_000
    # A timezone that a tzdata update removed after the save.
    board.role_schedules.save(
        CUSTODIAN_ROLE_KEY,
        owner_user_id=ALICE,
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        timezone="Mars/Olympus_Mons",
        next_run_at=now - 60,
    )
    packager = RoleSchedulePackager(board=board, sessions=sessions)

    await packager.package(now)
    await packager.package(now + 86_400)

    schedule = board.role_schedules.get(CUSTODIAN_ROLE_KEY, owner_user_id=ALICE)
    assert schedule is not None and schedule.enabled is False
    assert [item.kind for item in board.queue.list(owner_user_id=ALICE)] == ["scheduled_run"]


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


@pytest.mark.parametrize(
    ("approved", "listed"),
    [
        (
            [{"id": "delete_session__a", "kind": "delete_session", "session": "a"}],
            [
                "Approved changes; make these and no others:",
                '  - {"id": "delete_session__a", "kind": "delete_session", "session": "a"}',
            ],
        ),
        ([], ["Approved changes: none."]),
    ],
)
def test_a_click_on_a_committed_notice_lists_the_approved_changes(
    approved: list[dict[str, Any]], listed: list[str]
) -> None:
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
            "title": "1 session change to review",
            "action_id": "apply_changes",
            "action_label": "Apply selected changes",
            "inputs": {"delete_session__a": bool(approved)},
            "changes": approved,
        },
    )

    assert write_notice_click(item).splitlines()[3:-1] == [
        "Inputs:",
        f"  - delete_session__a: {json.dumps(bool(approved))}",
        *listed,
    ]


def test_a_request_for_managers_lists_the_live_tasks_without_one(
    board: BoardStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    def task(title: str, owner: str = ALICE, **fields: Any) -> str:
        task_id = next(_ids)
        board.tasks.create(task_id, owner_user_id=owner, title=title, **fields)
        return task_id

    task("Managed", manager_id=next(_ids))
    task("Archived", state="archived")
    task("Bob's", owner=BOB, manager_id=next(_ids))
    resolved = task("Resolved", state="agent-resolved")
    idle = task("Idle", state="idle")
    active = task("Active")
    item = AgentQueueItem(
        id=next(_ids),
        owner_user_id=ALICE,
        target_session_id=next(_ids),
        kind="spawn_managers",
        state="sending",
        seq=1,
        created_at=0,
        payload={},
    )
    writer = SpawnManagersWriter(board=board)
    header = [
        "[System: assign these unmanaged tasks to managers]",
        "Assign each task below to the best-fitting active manager, or create a manager "
        'when none fits. Follow "Assigning unmanaged tasks" in the Task broker manual.',
    ]

    whole = writer.write(item)
    monkeypatch.setattr("omnigent.server.agent_queue_packagers.MAX_SPAWN_TASKS", 2)
    capped = writer.write(item)

    assert whole == "\n".join(
        [
            *header,
            f"{active} — Active (active)",
            f"{idle} — Idle (idle)",
            f"{resolved} — Resolved (agent-resolved)",
        ]
    )
    assert capped == "\n".join(
        [
            *header,
            f"{active} — Active (active)",
            f"{idle} — Idle (idle)",
            "… and 1 more. The user can send a new request after you assign these.",
        ]
    )
    # Every live task of Bob's has a manager, so his request sends nothing.
    assert writer.write(dataclasses.replace(item, owner_user_id=BOB)) is None


async def test_a_waiting_event_reaches_the_broker_as_a_notice(
    board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
) -> None:
    sent: list[tuple[str, str]] = []

    async def send(session_id: str, text: str, **_: Any) -> SendResult:
        sent.append((session_id, text))
        return SendResult("sent")

    dispatcher = AgentQueueDispatcher(
        board=board,
        sessions=sessions,
        send_deps=send_deps,
        renderers={
            "events": EventNoticeWriter(
                board=board, conversations=sessions.conversation_store
            ).write
        },
        packagers=[BrokerPackager(board=board, sessions=sessions, send_deps=send_deps)],
        send=send,
        clock=lambda: time.time() + 31,
    )
    event_id = _event(board, ALICE, "host:h1")

    await asyncio.gather(*await dispatcher.run_once())

    [(session_id, text)] = sent
    assert session_id == await _broker(sessions)
    assert [entry["id"] for entry in json.loads(text)["clusters"][0]["events"]] == [event_id]
