"""Queue the work waiting for each user's broker, managers and custodian, and write queue messages.

Task events wait for the broker and the managers; the custodian gets a daily
session cleanup message at the time its owner chose.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.custodian_sweep import next_sweep_at
from omnigent.agent_tasks.event_batches import (
    broker_batches,
    cluster_by_tags,
    is_session_event,
    manager_batches,
)
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_role_session
from omnigent.agent_tasks.roles import (
    BROKER_ROLE_KEY,
    CUSTODIAN_ROLE_KEY,
    MANAGER_ROLE_KEY,
    builtin_role,
    session_role,
)
from omnigent.db.db_models import workspace_scope
from omnigent.entities import (
    LIVE_TASK_STATES,
    AgentQueueItem,
    Conversation,
    CustodianSweep,
    Task,
    TaskEvent,
)
from omnigent.server.agent_task_delivery import SessionSendDeps, session_has_live_turn

_logger = logging.getLogger(__name__)

# How many notices may carry an event its role neither routed nor handled.
MAX_DELIVERIES = 3
SESSION_DELETED_EVENT_TYPE = "session.deleted"
SESSION_TURN_FINISHED_EVENT_TYPE = "session.turn.finished"
_BROKER_PROMPT = "[System: route these events to managers]\n"
# About 20,000 tokens, v2's roster limit.
_ROSTER_MAX_CHARS = 80_000
_TURN_FINISHED_ADVICE = (
    "Read the recent session transcript to see what was done. "
    "Update the task overview and stamp all assets."
)
_CLICK_TEXT_SHOWN_CHARS = 500
#: The most tasks one request for managers lists.
MAX_SPAWN_TASKS = 100
_SPAWN_PROMPT = "[System: assign these unmanaged tasks to managers]"
_SPAWN_ADVICE = (
    "Assign each task below to the best-fitting active manager, or create a manager "
    'when none fits. Follow "Assigning unmanaged tasks" in the Task broker manual.'
)
_SWEEP_PROMPT = (
    "[System: daily session cleanup]\n"
    "This is your daily maintenance notice. Follow the Custodian manual, and be "
    "aggressive in suggesting session deletes."
)
# A cleanup message not yet handled covers the next day's too.
_UNHANDLED_STATES = ("queued", "sending", "sent")


class BrokerPackager:
    """Queues the events waiting for each user's broker, in v2's batches."""

    def __init__(
        self, *, board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
    ) -> None:
        """
        :param board: Holds the events and the queue.
        :param sessions: Where each user's broker session lives.
        :param send_deps: How a broker's runner is reached, to settle a turn that lost it.
        """
        role = builtin_role(BROKER_ROLE_KEY)
        assert role is not None
        self._role = role
        self._board = board
        self._sessions = sessions
        self._send_deps = send_deps

    async def package(self, now: int) -> None:
        """Queue the batches due at ``now``, user by user in each workspace."""
        waiting = await asyncio.to_thread(
            self._board.events.list_waiting_all_workspaces,
            state="unrouted",
            max_deliveries=MAX_DELIVERIES,
        )
        by_owner: defaultdict[tuple[int, str | None], list[TaskEvent]] = defaultdict(list)
        for event in waiting:
            by_owner[(event.workspace_id, event.owner_user_id)].append(event)
        for (workspace_id, owner), events in by_owner.items():
            try:
                with workspace_scope(workspace_id):
                    await self._package_for(owner, events, now)
            except Exception:
                _logger.exception("GlobalHub broker packaging failed for %s", owner)

    async def _package_for(self, owner: str | None, events: list[TaskEvent], now: int) -> None:
        settings = self._sessions.user_settings_store
        if await asyncio.to_thread(settings.get_globalhub_placement, user_id=owner) is None:
            # Without a GlobalHub host there is no broker yet; the events wait.
            return
        broker, _ = await ensure_role_session(self._role, user_id=owner, stores=self._sessions)
        idle = not await session_has_live_turn(broker, deps=self._send_deps)
        for batch in broker_batches(events, now=now, broker_idle=idle):
            await asyncio.to_thread(
                self._board.queue.enqueue,
                uuid.uuid4().hex,
                owner_user_id=owner,
                target_session_id=broker.id,
                kind="events",
                event_ids=[event.id for event in batch],
            )


class CustodianSweepPackager:
    """Queues each user's daily session cleanup message for their custodian when it is due."""

    def __init__(self, *, board: BoardStores, sessions: RoleSessionStores) -> None:
        """
        :param board: Holds the sweeps and the queue.
        :param sessions: Where each user's custodian session lives.
        """
        role = builtin_role(CUSTODIAN_ROLE_KEY)
        assert role is not None
        self._role = role
        self._board = board
        self._sessions = sessions

    async def package(self, now: int) -> None:
        """Queue the cleanup messages due at ``now``, user by user in each workspace."""
        due = await asyncio.to_thread(
            self._board.custodian_sweeps.list_due_all_workspaces, now=now
        )
        for sweep in due:
            try:
                with workspace_scope(sweep.workspace_id):
                    await self._package_for(sweep, now)
            except Exception:
                _logger.exception("GlobalHub custodian sweep failed for %s", sweep.owner_user_id)

    async def _package_for(self, sweep: CustodianSweep, now: int) -> None:
        assert sweep.next_run_at is not None
        # Days the server missed are skipped; only the pass that moves the time queues.
        moved = await asyncio.to_thread(
            self._board.custodian_sweeps.advance,
            sweep.id,
            due_at=sweep.next_run_at,
            next_run_at=next_sweep_at(sweep.local_time, sweep.timezone, after=now),
        )
        owner = sweep.owner_user_id
        settings = self._sessions.user_settings_store
        if not moved or (
            await asyncio.to_thread(settings.get_globalhub_placement, user_id=owner) is None
        ):
            return
        custodian, _ = await ensure_role_session(self._role, user_id=owner, stores=self._sessions)
        unhandled = await asyncio.to_thread(
            self._board.queue.list,
            owner_user_id=owner,
            states=_UNHANDLED_STATES,
            target_session_id=custodian.id,
        )
        if any(item.kind == "custodian_sweep" for item in unhandled):
            return
        await asyncio.to_thread(
            self._board.queue.enqueue,
            uuid.uuid4().hex,
            owner_user_id=owner,
            target_session_id=custodian.id,
            kind="custodian_sweep",
        )


class ManagerPackager:
    """Queues the events routed to each manager, in v2's batches."""

    def __init__(
        self, *, board: BoardStores, conversations: Any, send_deps: SessionSendDeps
    ) -> None:
        """
        :param board: Holds the events, the managers and the queue.
        :param conversations: Finds each manager's session.
        :param send_deps: How a manager's runner is reached, to settle a turn that lost it.
        """
        self._board = board
        self._conversations = conversations
        self._send_deps = send_deps

    async def package(self, now: int) -> None:
        """Queue the batches due at ``now``, manager by manager in each workspace."""
        waiting = await asyncio.to_thread(
            self._board.events.list_waiting_all_workspaces,
            state="routed",
            max_deliveries=MAX_DELIVERIES,
        )
        by_manager: defaultdict[tuple[int, str | None, str], list[TaskEvent]] = defaultdict(list)
        for event in waiting:
            if event.manager_id is not None:
                key = (event.workspace_id, event.owner_user_id, event.manager_id)
                by_manager[key].append(event)
        for (workspace_id, owner, manager_id), events in by_manager.items():
            try:
                with workspace_scope(workspace_id):
                    await self._package_for(owner, manager_id, events, now)
            except Exception:
                _logger.exception("GlobalHub packaging failed for manager %s", manager_id)

    async def _package_for(
        self, owner: str | None, manager_id: str, events: list[TaskEvent], now: int
    ) -> None:
        session = await asyncio.to_thread(self._manager_session, owner, manager_id)
        if session is None:
            # The manager list shows the missing session; the events wait.
            return
        idle = not await session_has_live_turn(session, deps=self._send_deps)
        for batch in manager_batches(events, now=now, manager_idle=idle):
            await asyncio.to_thread(
                self._board.queue.enqueue,
                uuid.uuid4().hex,
                owner_user_id=owner,
                target_session_id=session.id,
                kind="events",
                event_ids=[event.id for event in batch],
            )

    def _manager_session(self, owner: str | None, manager_id: str) -> Conversation | None:
        manager = self._board.managers.get(manager_id, owner_user_id=owner)
        if manager is None or manager.conversation_id is None:
            return None
        session = self._conversations.get_conversation(manager.conversation_id)
        return session if session is not None and not session.archived else None


class EventNoticeWriter:
    """Writes the notice text for ``events`` queue items when they are sent."""

    def __init__(self, *, board: BoardStores, conversations: Any) -> None:
        """
        :param board: Holds the events the items carry, the managers and their tasks.
        :param conversations: Finds the target and source sessions.
        """
        self._board = board
        self._conversations = conversations

    def write(self, item: AgentQueueItem) -> str | None:
        """
        Return the notice text, or ``None`` when none of the item's events still waits. Blocking.

        Leaves out events that passed to another role since they were queued,
        and dismisses events about sessions deleted before delivery, except
        the ``session.deleted`` events that report it.
        """
        target = self._conversations.get_conversation(item.target_session_id)
        role = session_role(target.labels) if target is not None else None
        if role is not None and role.key == BROKER_ROLE_KEY:
            return self._broker_notice(item)
        if role is not None and role.key == MANAGER_ROLE_KEY:
            return self._manager_notice(item)
        raise ValueError("an events notice goes only to a broker or a manager session")

    def _broker_notice(self, item: AgentQueueItem) -> str | None:
        waiting = self._waiting_events(item, "unrouted")
        if not waiting:
            return None
        clusters = [
            {"tags": _tags(cluster[0]), "events": [_broker_entry(event) for event in cluster]}
            for cluster in cluster_by_tags(waiting)
        ]
        return json.dumps(
            {"prompt": _BROKER_PROMPT, "clusters": clusters}, ensure_ascii=False, indent=2
        )

    def _manager_notice(self, item: AgentQueueItem) -> str | None:
        waiting = self._waiting_events(item, "routed")
        if not waiting:
            return None
        owner = item.owner_user_id
        manager = self._board.managers.get_by_conversation_id(
            item.target_session_id, owner_user_id=owner
        )
        roster = (
            self._board.tasks.list(
                owner_user_id=owner, states=LIVE_TASK_STATES, manager_id=manager.id
            )
            if manager is not None
            else []
        )
        return f"{_manager_events_text(waiting)}\n\n{_roster_text(roster)}"

    def _waiting_events(self, item: AgentQueueItem, state: str) -> list[TaskEvent]:
        owner = item.owner_user_id
        waiting: list[TaskEvent] = []
        for event in self._board.events.list(
            owner_user_id=owner, states=[state], queue_item_ids=[item.id]
        ):
            if self._about_deleted_session(event):
                self._board.events.dismiss(event.id, owner_user_id=owner)
            else:
                waiting.append(event)
        return waiting

    def _about_deleted_session(self, event: TaskEvent) -> bool:
        return (
            is_session_event(event)
            and event.event_type != SESSION_DELETED_EVENT_TYPE
            and event.source_session_id is not None
            and self._conversations.get_conversation(event.source_session_id) is None
        )


class SpawnManagersWriter:
    """Writes the broker's message for a ``spawn_managers`` queue item when it is sent."""

    def __init__(self, *, board: BoardStores) -> None:
        """
        :param board: Holds the tasks the message lists.
        """
        self._board = board

    def write(self, item: AgentQueueItem) -> str | None:
        """
        Return the message that lists the owner's live tasks without a manager. Blocking.

        :returns: The message, with the tasks in board order, or ``None`` when
            every live task has a manager.
        """
        owner = item.owner_user_id
        tasks = self._board.tasks.list(
            owner_user_id=owner, states=LIVE_TASK_STATES, unmanaged=True, limit=MAX_SPAWN_TASKS
        )
        if not tasks:
            return None
        lines = [_SPAWN_PROMPT, _SPAWN_ADVICE, *(_task_line(task) for task in tasks)]
        if len(tasks) == MAX_SPAWN_TASKS:
            total = self._board.tasks.count(
                owner_user_id=owner, states=LIVE_TASK_STATES, unmanaged=True
            )
            if total > len(tasks):
                lines.append(
                    f"… and {total - len(tasks)} more. The user can send a new request "
                    "after you assign these."
                )
        return "\n".join(lines)


def write_custodian_sweep(_item: AgentQueueItem) -> str:
    """Write the custodian's daily session cleanup message; it is the same every day."""
    return _SWEEP_PROMPT


def write_notice_click(item: AgentQueueItem) -> str:
    """Write the custodian's message for a ``notice_click`` queue item, from its payload."""
    click = item.payload
    label = click.get("action_label")
    lines = [
        f"[System notice click — notice {click['notice_id']}]",
        f"Title: {click['title']}",
        f"Action clicked: {click['action_id']}" + (f" ({label})" if label else ""),
    ]
    if click.get("publisher_label"):
        lines.append(f"Published by: {click['publisher_label']}")
    inputs: dict[str, Any] = click.get("inputs") or {}
    if inputs:
        lines.append("Inputs:")
        for input_id, value in inputs.items():
            if isinstance(value, str) and len(value) > _CLICK_TEXT_SHOWN_CHARS:
                value = value[:_CLICK_TEXT_SHOWN_CHARS] + "…"
            lines.append(f"  - {input_id}: {json.dumps(value, ensure_ascii=False)}")
    lines.append(
        "Interpret this click per the notice's intent and the Custodian manual. Read the "
        'notice itself with system_notice(action="get") if you need its full body.'
    )
    return "\n".join(lines)


def _tags(event: TaskEvent) -> list[dict[str, str]]:
    return [{"tag_type": tag.tag_type, "tag": tag.tag} for tag in event.tags]


def _broker_entry(event: TaskEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "title": event.title,
        "state": event.state,
        "source": event.source,
        "source_key": event.source_key,
        "created_at": event.created_at,
        "tags": _tags(event),
        "payload": event.payload,
    }


def _manager_events_text(events: Sequence[TaskEvent]) -> str:
    """Write v2's manager notice lines, each with its event id."""
    task_ids = list(dict.fromkeys(event.task_id for event in events if event.task_id))
    unassigned = sum(1 for event in events if event.task_id is None)
    if len(task_ids) == 1 and not unassigned:
        scope = f"task {task_ids[0]}"
    elif task_ids:
        scope = f"{len(task_ids)} tasks: " + ", ".join(task_ids)
    else:
        scope = "this manager"
    lines = [f"[System: {len(events)} event(s) routed to {scope} — triage or act]"]
    if unassigned:
        lines.append(f"[{unassigned} manager-routed event(s) have no task]")
    by_session: dict[str, list[TaskEvent]] = {}
    for event in events:
        if is_session_event(event) and event.source_session_id:
            by_session.setdefault(event.source_session_id, []).append(event)
        else:
            lines.append(_event_line(event))
    lines.extend(_session_entry(session_events) for session_events in by_session.values())
    return "\n".join(lines)


def _event_line(event: TaskEvent) -> str:
    label = f"[task:{event.task_id}]" if event.task_id else "[manager-routed; task unassigned]"
    return f"- {label} [event:{event.id}] {event.event_type}: {event.title!r} (routed)"


def _session_entry(events: Sequence[TaskEvent]) -> str:
    """Write one entry for a session's events; a deletion supersedes the rest."""
    deleted = [event for event in events if event.event_type == SESSION_DELETED_EVENT_TYPE]
    first = deleted[-1] if deleted else events[0]
    ids = ", ".join(event.id for event in events)
    ids_label = f"[event:{ids}]" if len(events) == 1 else f"[events:{ids}]"
    title = first.payload.get("session_title", "?")
    session_line = f"  Session ID: {first.payload.get('session_id') or first.source_session_id}"
    project = first.payload.get("project_name")
    project_lines = [f"  Project: {project}"] if project else []
    if deleted:
        return "\n".join(
            [
                f"- {ids_label} {SESSION_DELETED_EVENT_TYPE}: Session '{title}' was deleted",
                *project_lines,
                session_line,
                "  The session no longer exists; the user deleted it. "
                "Its task may already be finished.",
            ]
        )
    if first.event_type == SESSION_TURN_FINISHED_EVENT_TYPE and len(events) == 1:
        return "\n".join(
            [
                f"- {ids_label} {first.event_type}: Session '{title}' finished a turn",
                *project_lines,
                session_line,
                f"  {_TURN_FINISHED_ADVICE}",
            ]
        )
    if all(event.event_type == SESSION_TURN_FINISHED_EVENT_TYPE for event in events):
        return "\n".join(
            [
                f"- {ids_label} {first.event_type}: Session '{title}' finished "
                f"{len(events)} turns since last check",
                session_line,
                f"  {_TURN_FINISHED_ADVICE}",
            ]
        )
    if len(events) == 1:
        return _event_line(first)
    return f"- {ids_label} {len(events)} events for session '{title}' (routed)"


def _roster_text(tasks: Sequence[Task]) -> str:
    """Write the ``[Task roster]`` section: the manager's live tasks in board order."""
    lines = ["[Task roster]"]
    used = 0
    for index, task in enumerate(tasks):
        line = _task_line(task)
        if index and used + len(line) > _ROSTER_MAX_CHARS:
            lines.append(
                f"… and {len(tasks) - index} more; list them with "
                "GET /v1/agent-tasks?manager_conversation_id=<your session id>"
            )
            break
        lines.append(line)
        used += len(line) + 1
    if not tasks:
        lines.append("(no tasks yet)")
    return "\n".join(lines)


def _task_line(task: Task) -> str:
    return f"{task.id} — {task.title} ({task.state})"
