"""Tests for queueing broker work and writing its notices (``agent_queue_packagers``)."""

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
from omnigent.entities import AgentQueueItem, EventTag
from omnigent.server import agent_queue_packagers
from omnigent.server.agent_queue_dispatcher import AgentQueueDispatcher
from omnigent.server.agent_queue_packagers import (
    BrokerPackager,
    EventNoticeWriter,
    ManagerPackager,
    RoleSchedulePackager,
    ScheduledRunWriter,
    SpawnManagersWriter,
    write_notice_click,
)
from omnigent.server.agent_task_delivery import SendResult, SessionSendDeps
from omnigent.server.routes._sessions.helpers import SessionLiveness
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore
from omnigent.stores.watch_item_store import WatchObservation

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
    board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
) -> None:
    manager_id, manager_session = _manager(board, sessions)
    orphan_id, _ = _manager(board, sessions, "Former manager", archived=True)
    routed = [_event(board, manager_id=manager_id) for _ in range(2)]
    stranded = _event(board, manager_id=orphan_id)
    packager = ManagerPackager(
        board=board, conversations=sessions.conversation_store, send_deps=send_deps
    )

    await packager.package(int(time.time()) + 31)

    [item] = board.queue.list(owner_user_id=ALICE)
    carried = board.events.list(owner_user_id=ALICE, queue_item_ids=[item.id])
    assert (item.target_session_id, [event.id for event in carried]) == (manager_session, routed)
    # The archived session cannot take the event, so it waits.
    stranded_event = board.events.get(stranded, owner_user_id=ALICE)
    assert stranded_event is not None and stranded_event.queue_item_id is None


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


async def test_a_manager_notice_lists_its_events_by_id_and_ends_with_the_roster(
    board: BoardStores, sessions: RoleSessionStores
) -> None:
    manager_id, manager_session = _manager(board, sessions)
    task_id = next(_ids)
    board.tasks.create(task_id, owner_user_id=ALICE, title="S3 retries", manager_id=manager_id)
    board.tasks.create(
        next(_ids),
        owner_user_id=ALICE,
        title="Old work",
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
            "Update the task overview, stamp all assets, then assign the event to its task.",
            f"- [event:{deleted}] session.deleted: Session 'Old work' was deleted",
            f"  Session ID: {gone_session}",
            "  The session no longer exists; the user deleted it. "
            "Its task may already be finished.",
        ]
    )
    dismissed = board.events.get(stale_turn, owner_user_id=ALICE)
    assert dismissed is not None and dismissed.state == "dismissed"


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
            source="github",
            source_key=f"https://example.com/pr/{n}",
            offsets={"state": "open"},
            title=f"PR {n}",
        )
        # 40 days ago, an hour apart, so the run lists them in this order.
        board.watch_items.observe(watcher, [seen], session_id=None, now=now - 40 * day + n * 3_600)
    board.watch_items.finish(
        watcher.id,
        [("github", "https://example.com/pr/0")],
        owner_user_id=ALICE,
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
        '- github https://example.com/pr/1 "PR 1": state=open (checked 2026-12-06 09:00 UTC)',
        '- github https://example.com/pr/2 "PR 2": state=open (checked 2026-12-06 10:00 UTC)',
    ]
    assert text.splitlines()[-3:] == [
        "[Tracked items] (the least recently checked first)",
        *pr_lines,
    ]
    assert short.splitlines()[-2:] == [
        pr_lines[0],
        '… and 1 more; page through them with watch_items action "list".',
    ]


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
