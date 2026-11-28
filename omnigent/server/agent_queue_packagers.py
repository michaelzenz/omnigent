"""Queue the work waiting for each user's GlobalHub sessions, and write queue messages.

Task events wait for the broker, or for the sessions that follow their sources,
e.g. managers; a role with a schedule, such as the custodian or a watcher, gets
its run message when its schedule comes due.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.event_batches import (
    broker_batches,
    cluster_by_tags,
    is_session_event,
    session_batches,
)
from omnigent.agent_tasks.role_schedules import next_run_at
from omnigent.agent_tasks.role_sessions import (
    RoleSessionStores,
    ensure_role_session,
    ensure_watcher_session,
    is_role_session_current,
)
from omnigent.agent_tasks.roles import (
    BROKER_ROLE_KEY,
    CUSTODIAN_ROLE_KEY,
    MANAGER_ROLE_KEY,
    builtin_role,
    session_role,
)
from omnigent.db.db_models import workspace_scope
from omnigent.db.utils import now_epoch
from omnigent.entities import (
    LIVE_TASK_STATES,
    MAX_WATCHER_CURSOR_BYTES,
    WATCHER_ROLE_KEY_PREFIX,
    AgentQueueItem,
    Conversation,
    RoleSchedule,
    Subscription,
    Task,
    TaskEvent,
    Watcher,
    WatchItem,
)
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.agent_task_delivery import SessionSendDeps, session_has_live_turn
from omnigent.stores.subscription_store import BROKER_ROUTING
from omnigent.stores.watch_item_store import WatchFinish

_logger = logging.getLogger(__name__)

# How many notices may carry an event its role neither routed nor handled.
MAX_DELIVERIES = 3
SESSION_DELETED_EVENT_TYPE = "session.deleted"
SESSION_START_FAILED_EVENT_TYPE = "session.start.failed"
SESSION_TURN_FINISHED_EVENT_TYPE = "session.turn.finished"
_BROKER_PROMPT = "[System: route these events to tasks or managers]\n"
_MAX_FOLLOWERS_IN_BROKER_NOTICE = 20
_TURN_FINISHED_ADVICE = (
    "Read the recent session transcript to see what was done. "
    "Update the task overview, stamp all assets, then assign the event to its task."
)
_CLICK_TEXT_SHOWN_CHARS = 500
#: The most tasks one request for managers lists.
MAX_SPAWN_TASKS = 100
_SPAWN_PROMPT = "[System: assign these unmanaged tasks to managers]"
_SPAWN_ADVICE = (
    "Assign each task below to the best-fitting active manager, or create a manager "
    'when none fits. Follow "Assigning unmanaged tasks" in the Task broker manual.'
)
_CUSTODIAN_RUN_PROMPT = (
    "[System: session cleanup]\n"
    "This is your scheduled maintenance notice. Follow the Custodian manual, and be "
    "aggressive in suggesting session deletes."
)
_WATCHER_RUN_PROMPT = (
    "[System: scheduled watcher run]\n"
    "Follow your manual: check what it says to watch, and the sources listed below.\n"
    '1. Report the current state of each item with watch_items action "observe". The '
    "server compares it with your last run and reports each change; do not report "
    "changes any other way.\n"
    '2. Mark finished items with watch_items action "done", with a short summary of why.\n'
    "3. To remember where this run stopped, e.g. the time of the newest message that you "
    f"read, pass a cursor of at most {MAX_WATCHER_CURSOR_BYTES} bytes to watch_items action "
    '"observe". Your next run message shows it under [Cursor].\n'
    "Do not start sessions or change anything."
)
_SOURCE_TYPES_PROMPT = "[System: assign source types to watchers]"
_SOURCE_TYPES_ADVICE = (
    "Sessions follow sources of the types below, and no watcher checks them yet. Follow "
    '"Assigning source types" in the Custodian manual: assign each type to the watcher '
    "whose manual covers it with source_watchers, and leave a type unassigned when no "
    "manual covers it."
)
_SOURCE_ROUTING_ADVICE = (
    'Types without a routing. Follow "Assigning source types" in the Custodian manual: set '
    'each type with source_watchers action "route", to "broker" for a stream in which one '
    "source carries many topics, e.g. a direct message, a channel or an inbox, else to "
    '"followers":'
)
# The run message shows the items most in need of a check; the watcher pages
# through the rest with its tool. About 5,000 tokens at most, so a large
# tracker cannot crowd out the manual.
_RUN_ITEMS_SHOWN = 50
_RUN_ITEMS_MAX_CHARS = 20_000
# A done item stays this long, so a watcher that finds it again does not report it as new.
_DONE_WATCH_ITEMS_KEPT_S = 30 * 24 * 60 * 60
_QUIET_WATCH_ITEMS_FINISHED_AFTER_S = 14 * 24 * 60 * 60
_QUIET_DONE_SUMMARY = "No activity for 14 days"
_SUBSCRIPTIONS_KEPT_AFTER_DONE_S = 7 * 24 * 60 * 60
_DONE_RECHECK_S = 24 * 60 * 60
_SOURCE_TYPE_EXAMPLE_CHARS = 200
# A run message not yet handled covers the next run too.
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


class RoleSchedulePackager:
    """Queues the run message of each due role schedule for its owner's role session."""

    def __init__(self, *, board: BoardStores, sessions: RoleSessionStores) -> None:
        """
        :param board: Holds the schedules and the queue.
        :param sessions: Where each user's role sessions live.
        """
        self._board = board
        self._sessions = sessions

    async def package(self, now: int) -> None:
        """Queue the run messages due at ``now``, schedule by schedule in each workspace."""
        due = await asyncio.to_thread(self._board.role_schedules.list_due_all_workspaces, now=now)
        for schedule in due:
            try:
                with workspace_scope(schedule.workspace_id):
                    await self._package_for(schedule, now)
            except Exception:
                _logger.exception(
                    "GlobalHub scheduled run of %s failed for %s",
                    schedule.role_key,
                    schedule.owner_user_id,
                )

    async def _package_for(self, schedule: RoleSchedule, now: int) -> None:
        assert schedule.next_run_at is not None
        # Runs the server missed turn into one; only the pass that moves the time queues.
        moved = await asyncio.to_thread(
            self._board.role_schedules.advance,
            schedule.id,
            due_at=schedule.next_run_at,
            next_run_at=_next_run_or_off(schedule, now),
        )
        owner = schedule.owner_user_id
        settings = self._sessions.user_settings_store
        if not moved or (
            await asyncio.to_thread(settings.get_globalhub_placement, user_id=owner) is None
        ):
            return
        if await asyncio.to_thread(self._fallback_without_sources, schedule):
            return
        session = await self._session_for(schedule, now)
        if session is None:
            return
        unhandled = await asyncio.to_thread(
            self._board.queue.list,
            owner_user_id=owner,
            states=_UNHANDLED_STATES,
            target_session_id=session.id,
        )
        if any(
            item.kind == "scheduled_run" and item.payload.get("role_key") == schedule.role_key
            for item in unhandled
        ):
            return
        await asyncio.to_thread(
            self._board.queue.enqueue,
            uuid.uuid4().hex,
            owner_user_id=owner,
            target_session_id=session.id,
            kind="scheduled_run",
            payload={"role_key": schedule.role_key},
        )

    async def _session_for(self, schedule: RoleSchedule, now: int) -> Conversation | None:
        """Return the session that gets the schedule's run, or ``None`` when its role is gone."""
        owner = schedule.owner_user_id
        if schedule.role_key.startswith(WATCHER_ROLE_KEY_PREFIX):
            watcher_id = schedule.role_key.removeprefix(WATCHER_ROLE_KEY_PREFIX)
            try:
                session, _ = await ensure_watcher_session(
                    watcher_id, user_id=owner, stores=self._sessions, watchers=self._board.watchers
                )
            except OmnigentError as exc:
                if exc.code != ErrorCode.NOT_FOUND:
                    raise
                # The watcher is gone, so its schedule goes too.
                await asyncio.to_thread(
                    self._board.role_schedules.delete, schedule.role_key, owner_user_id=owner
                )
                return None
            await asyncio.to_thread(self._tidy_watcher, watcher_id, owner, now)
            return session
        role = builtin_role(schedule.role_key)
        if role is None or not role.singleton:
            return None
        session, _ = await ensure_role_session(role, user_id=owner, stores=self._sessions)
        return session

    def _fallback_without_sources(self, schedule: RoleSchedule) -> bool:
        """Whether the schedule is the fallback watcher's and no source waits for it. Blocking."""
        if not schedule.role_key.startswith(WATCHER_ROLE_KEY_PREFIX):
            return False
        owner = schedule.owner_user_id
        watcher = self._board.watchers.get(
            schedule.role_key.removeprefix(WATCHER_ROLE_KEY_PREFIX), owner_user_id=owner
        )
        return (
            watcher is not None
            and watcher.builtin
            and not self._board.subscriptions.list_unassigned(owner_user_id=owner)
        )

    def _tidy_watcher(self, watcher_id: str, owner: str | None, now: int) -> None:
        """Finish the items quiet for 14 days, end the subscriptions to sources done for a
        week, and delete old done items. Blocking."""
        quiet = self._board.watch_items.list_quiet(
            watcher_id, owner_user_id=owner, before=now - _QUIET_WATCH_ITEMS_FINISHED_AFTER_S
        )
        watcher = self._board.watchers.get(watcher_id, owner_user_id=owner) if quiet else None
        if watcher is not None:
            self._board.watch_items.finish(
                watcher,
                [
                    WatchFinish(item.source, item.source_key, summary=_QUIET_DONE_SUMMARY)
                    for item in quiet
                ],
                session_id=None,
                now=now,
            )
        done = self._board.watch_items.list_done_before(
            watcher_id, owner_user_id=owner, before=now - _SUBSCRIPTIONS_KEPT_AFTER_DONE_S
        )
        for item in done:
            self._board.subscriptions.delete_for_keys(
                [item.source_key], owner_user_id=owner, created_before=item.done_at
            )
        self._board.watch_items.purge_done(
            watcher_id, owner_user_id=owner, before=now - _DONE_WATCH_ITEMS_KEPT_S
        )


def _next_run_or_off(schedule: RoleSchedule, now: int) -> int | None:
    """Return the schedule's next run after ``now``, or ``None`` to turn off a rule that fails."""
    try:
        return next_run_at(
            schedule.rrule, schedule.timezone, after=now, anchor=schedule.created_at
        )
    except (ValueError, ZoneInfoNotFoundError):
        _logger.warning(
            "GlobalHub turned off the %s schedule of %s: %r in %s has no next run",
            schedule.role_key,
            schedule.owner_user_id,
            schedule.rrule,
            schedule.timezone,
            exc_info=True,
        )
        return None


class SubscriberPackager:
    """Queues the events routed to each session, in v2's batches.

    A manager's session gets an event until the manager handles it, up to
    :data:`MAX_DELIVERIES` notices; while the session is gone or archived, its
    events wait, and deleting the manager gives them back to the broker. Any
    other session gets each event once. When such a session is gone, archived,
    or a role session that no longer serves its role, its events are
    dismissed and its subscriptions end.
    """

    def __init__(
        self, *, board: BoardStores, sessions: RoleSessionStores, send_deps: SessionSendDeps
    ) -> None:
        """
        :param board: Holds the events, the managers, the subscriptions and the queue.
        :param sessions: Finds each target session, and whether a role session is current.
        :param send_deps: How a session's runner is reached, to settle a turn that lost it.
        """
        self._board = board
        self._sessions = sessions
        self._send_deps = send_deps

    async def package(self, now: int) -> None:
        """Queue the batches due at ``now``, session by session in each workspace."""
        waiting = await asyncio.to_thread(
            self._board.events.list_waiting_all_workspaces,
            state="routed",
            max_deliveries=MAX_DELIVERIES,
        )
        by_target: defaultdict[tuple[int, str | None, str], list[TaskEvent]] = defaultdict(list)
        for event in waiting:
            if event.target_session_id is not None:
                key = (event.workspace_id, event.owner_user_id, event.target_session_id)
                by_target[key].append(event)
        for (workspace_id, owner, target_id), events in by_target.items():
            try:
                with workspace_scope(workspace_id):
                    await self._package_for(owner, target_id, events, now)
            except Exception:
                _logger.exception("GlobalHub packaging failed for session %s", target_id)

    async def _package_for(
        self, owner: str | None, target_id: str, events: list[TaskEvent], now: int
    ) -> None:
        target, manager = await asyncio.to_thread(self._target, owner, target_id)
        if manager:
            if target is None or target.archived:
                # The manager list shows the missing session; the events wait.
                return
        else:
            if target is None:
                await asyncio.to_thread(self._drop_target, owner, target_id, events)
                return
            delivered = [event for event in events if event.delivery_count > 0]
            for event in delivered:
                await asyncio.to_thread(self._board.events.dismiss, event.id, owner_user_id=owner)
            events = [event for event in events if event.delivery_count == 0]
        idle = not await session_has_live_turn(target, deps=self._send_deps)
        for batch in session_batches(events, now=now, session_idle=idle):
            await asyncio.to_thread(
                self._board.queue.enqueue,
                uuid.uuid4().hex,
                owner_user_id=owner,
                target_session_id=target.id,
                kind="events",
                event_ids=[event.id for event in batch],
            )

    def _target(self, owner: str | None, target_id: str) -> tuple[Conversation | None, bool]:
        """Return the session that takes the events, and whether it is a manager's. Blocking.

        The session is ``None`` when it cannot take events. A manager's session
        counts as a manager's even when it is gone or archived.
        """
        if self._board.managers.get_by_conversation_id(target_id, owner_user_id=owner):
            return self._sessions.conversation_store.get_conversation(target_id), True
        target = self._sessions.conversation_store.get_conversation(target_id)
        if target is None or target.archived:
            return None, False
        if session_role(target.labels) is not None and not is_role_session_current(
            target,
            user_id=owner,
            stores=self._sessions,
            managers=self._board.managers,
            watchers=self._board.watchers,
        ):
            return None, False
        return target, False

    def _drop_target(self, owner: str | None, target_id: str, events: list[TaskEvent]) -> None:
        """Dismiss the events for a session that cannot take them, and end its subscriptions."""
        for event in events:
            self._board.events.dismiss(event.id, owner_user_id=owner)
        self._board.subscriptions.delete_for_session(target_id, owner_user_id=owner)


class EventNoticeWriter:
    """Writes the notice text for ``events`` queue items when they are sent."""

    def __init__(self, *, board: BoardStores, conversations: Any) -> None:
        """
        :param board: Holds the events the items carry, and the unmanaged tasks.
        :param conversations: Finds the target and source sessions.
        """
        self._board = board
        self._conversations = conversations

    def write(self, item: AgentQueueItem) -> str | None:
        """
        Return the notice text, or ``None`` when none of the item's events still waits. Blocking.

        Leaves out events that passed to another session since they were
        queued, and dismisses events about sessions deleted before delivery,
        except the ``session.deleted`` events that report it. The broker gets
        its routing notice for unrouted events, and a manager its notice for
        routed events; any other session, or the broker for the sources it
        follows, gets a ``[System: event]`` notice.
        """
        target = self._conversations.get_conversation(item.target_session_id)
        role = session_role(target.labels) if target is not None else None
        if role is not None and role.key == BROKER_ROLE_KEY:
            routing = self._broker_notice(item)
            if routing is not None:
                return routing
        waiting = self._waiting_events(item, "routed")
        if not waiting:
            return None
        if role is not None and role.key == MANAGER_ROLE_KEY:
            # The roster rides each turn as turn context (see role_turn_context).
            return _manager_events_text(waiting, self._matched_tasks(item.owner_user_id, waiting))
        return _subscriber_events_text(waiting)

    def _matched_tasks(self, owner: str | None, events: Sequence[TaskEvent]) -> dict[str, Task]:
        matched: dict[str, Task] = {}
        for task_id in dict.fromkeys(event.task_id for event in events if event.task_id):
            task = self._board.tasks.get(task_id, owner_user_id=owner)
            if task is not None:
                matched[task_id] = task
        return matched

    def _broker_notice(self, item: AgentQueueItem) -> str | None:
        waiting = self._waiting_events(item, "unrouted")
        if not waiting:
            return None
        owner = item.owner_user_id
        routings: dict[str, str | None] = {}
        followed: dict[str, dict[str, Any]] = {}
        for event in waiting:
            key, source = event.source_key, event.source
            if not key or not source or key in followed:
                continue
            if source not in routings:
                routings[source] = self._board.subscriptions.routing(
                    owner_user_id=owner, source_type=source
                )
            if routings[source] == BROKER_ROUTING:
                followed[key] = self._followed_by(owner, key)
        clusters = [
            {
                "tags": _tags(cluster[0]),
                "events": [
                    {**_broker_entry(event), **followed.get(event.source_key or "", {})}
                    for event in cluster
                ],
            }
            for cluster in cluster_by_tags(waiting)
        ]
        return json.dumps(
            {"prompt": _BROKER_PROMPT, "clusters": clusters}, ensure_ascii=False, indent=2
        )

    def _followed_by(self, owner: str | None, source_key: str) -> dict[str, Any]:
        followers = self._board.subscriptions.subscribers(
            owner_user_id=owner, source_key=source_key
        )
        shown = followers[-_MAX_FOLLOWERS_IN_BROKER_NOTICE:][::-1]
        if not shown:
            return {}
        facts: dict[str, Any] = {
            "followed_by": [self._follower(owner, session_id, source_key) for session_id in shown]
        }
        if len(followers) > len(shown):
            facts["more_followers"] = len(followers) - len(shown)
        return facts

    def _follower(self, owner: str | None, session_id: str, source_key: str) -> dict[str, Any]:
        session = self._conversations.get_conversation(session_id)
        follower: dict[str, Any] = {
            "session_id": session_id,
            "title": session.title if session is not None else None,
        }
        note = next(
            (
                subscription.note
                for subscription in self._board.subscriptions.list_for_session(
                    session_id, owner_user_id=owner
                )
                if subscription.source_key == source_key and subscription.note
            ),
            None,
        )
        if note:
            follower["note"] = note
        tasks: list[dict[str, str]] = []
        for worker in self._board.workers.list_for_session(session_id, owner_user_id=owner):
            task = self._board.tasks.get(worker.task_id, owner_user_id=owner)
            if task is not None and task.state in LIVE_TASK_STATES:
                tasks.append({"id": task.id, "title": task.title})
        follower["tasks"] = tasks
        return follower

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


class ScheduledRunWriter:
    """Writes the message for a ``scheduled_run`` queue item when it is sent."""

    def __init__(self, *, board: BoardStores) -> None:
        """
        :param board: Holds the watchers that scheduled runs go to.
        """
        self._board = board

    def write(self, item: AgentQueueItem) -> str | None:
        """
        Return the run message for the role the item names. Blocking.

        A watcher's message shows the cursor that it saved, if any, then lists
        its active items with the offsets it saw last, then the sources that
        sessions follow and that it should check:
        those of its source types, or for the fallback watcher, those of the
        types without a watcher. Each followed source carries its note, e.g.
        how to read a source that is hard to read. The custodian's message also
        lists the source types without a watcher.

        :returns: The message, or ``None`` for a role without one, a watcher
            that was deleted after the run was queued, or a fallback watcher
            with no source to check.
        """
        role_key = item.payload.get("role_key")
        owner = item.owner_user_id
        if role_key == CUSTODIAN_ROLE_KEY:
            pending = _source_types_text(self._board, owner)
            return f"{_CUSTODIAN_RUN_PROMPT}\n\n{pending}" if pending else _CUSTODIAN_RUN_PROMPT
        if not isinstance(role_key, str) or not role_key.startswith(WATCHER_ROLE_KEY_PREFIX):
            return None
        watcher_id = role_key.removeprefix(WATCHER_ROLE_KEY_PREFIX)
        watcher = self._board.watchers.get(watcher_id, owner_user_id=owner)
        if watcher is None:
            return None
        followed = self._followed(watcher)
        if watcher.builtin and not followed:
            return None
        notes = _note_by_source(followed)
        parts = [_WATCHER_RUN_PROMPT]
        if watcher.cursor:
            parts.append(f"[Cursor] {watcher.cursor}")
        parts.append(self._tracked_items_text(watcher, notes))
        sources = self._followed_sources_text(watcher, followed, notes)
        if sources is not None:
            parts.append(sources)
        return "\n\n".join(parts)

    def _followed(self, watcher: Watcher) -> list[Subscription]:
        """Return the subscriptions the watcher checks, oldest first."""
        owner = watcher.owner_user_id
        subscriptions = self._board.subscriptions
        if watcher.builtin:
            return subscriptions.list_unassigned(owner_user_id=owner)
        types = subscriptions.types_for_watcher(watcher.id, owner_user_id=owner)
        return subscriptions.list_for_types(types, owner_user_id=owner) if types else []

    def _followed_sources_text(
        self,
        watcher: Watcher,
        followed: Sequence[Subscription],
        notes: Mapping[tuple[str, str], str],
    ) -> str | None:
        """Write the ``[Followed sources]`` section: new sources, and done ones due a check.

        A source that the watcher does not track yet is new. An active item is
        in ``[Tracked items]`` already; a done item gets a check once a day, to
        catch a reopen. Each source comes with its note.
        """
        keys = list(dict.fromkeys((sub.source_type, sub.source_key) for sub in followed))
        if not keys:
            return None
        owner = watcher.owner_user_id
        tracked = {
            (item.source, item.source_key): item
            for item in self._board.watch_items.find(watcher.id, keys, owner_user_id=owner)
        }
        now = now_epoch()
        lines: list[str] = []
        for source_type, source_key in keys:
            item = tracked.get((source_type, source_key))
            if item is None:
                line = (
                    f"- {source_type} {source_key}: not tracked yet; observe it with this "
                    "source type to start"
                )
            elif item.status == "done" and now - item.checked_at >= _DONE_RECHECK_S:
                done = _utc_day(item.done_at or item.checked_at)
                line = (
                    f"- {source_type} {source_key}: done since {done}; observe it to see "
                    "whether it reopened"
                )
            else:
                continue
            lines.append(_with_note(line, notes.get((source_type, source_key))))
        if not lines:
            return None
        shown = lines[:_RUN_ITEMS_SHOWN]
        if len(lines) > len(shown):
            shown.append(f"… and {len(lines) - len(shown)} more; they come in the next runs.")
        return "\n".join(["[Followed sources] (sessions follow these)", *shown])

    def _tracked_items_text(self, watcher: Watcher, notes: Mapping[tuple[str, str], str]) -> str:
        """Write the ``[Tracked items]`` section: active items, least recently checked first.

        An item that sessions follow comes with its note.
        """
        owner = watcher.owner_user_id
        items = self._board.watch_items.stalest(
            watcher.id, owner_user_id=owner, limit=_RUN_ITEMS_SHOWN
        )
        lines = ["[Tracked items] (the least recently checked first)"]
        used = 0
        for index, tracked in enumerate(items):
            line = _with_note(
                _watch_item_line(tracked), notes.get((tracked.source, tracked.source_key))
            )
            if index and used + len(line) > _RUN_ITEMS_MAX_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
        shown = len(lines) - 1
        total = (
            self._board.watch_items.count(watcher.id, owner_user_id=owner)
            if len(items) == _RUN_ITEMS_SHOWN or shown < len(items)
            else len(items)
        )
        if not items:
            lines.append("(none yet)")
        elif total > shown:
            lines.append(
                f'… and {total - shown} more; page through them with watch_items action "list".'
            )
        return "\n".join(lines)


class SourceTypesWriter:
    """Writes the custodian's message for a ``source_types`` queue item when it is sent."""

    def __init__(self, *, board: BoardStores) -> None:
        """
        :param board: Holds the subscriptions and the watchers the message lists.
        """
        self._board = board

    def write(self, item: AgentQueueItem) -> str | None:
        """
        Return the message that lists the source types without a watcher. Blocking.

        :returns: The message, with each type and the owner's watchers, or
            ``None`` when every type in use has a watcher.
        """
        return _source_types_text(self._board, item.owner_user_id)


def _source_types_text(board: BoardStores, owner: str | None) -> str | None:
    """List the types without a watcher, the watchers, and the types without a routing."""
    uses = board.subscriptions.source_types(owner_user_id=owner)
    unassigned = [use for use in uses if use.watcher_id is None]
    unrouted = [use for use in uses if use.routing is None]
    if not unassigned and not unrouted:
        return None
    examples: dict[str, str] = {}
    for subscription in board.subscriptions.list_for_types(
        [use.source_type for use in uses], owner_user_id=owner
    ):
        examples.setdefault(subscription.source_type, subscription.source_key)

    def example(source_type: str) -> str:
        text = examples.get(source_type, "")[:_SOURCE_TYPE_EXAMPLE_CHARS]
        return f", e.g. {text}" if text else ""

    lines = [_SOURCE_TYPES_PROMPT]
    if unassigned:
        watchers = [
            watcher for watcher in board.watchers.list(owner_user_id=owner) if not watcher.builtin
        ]
        lines.extend([_SOURCE_TYPES_ADVICE, "Types without a watcher:"])
        lines.extend(
            f"- {use.source_type}: {use.subscriptions} subscription(s){example(use.source_type)}"
            for use in unassigned
        )
        lines.append("Watchers (read a manual with pmv2_read path /v1/agent-tasks/watchers/<id>):")
        if watchers:
            lines.extend(
                f"- {watcher.id} {watcher.name}: {watcher.description or '(no description)'}"
                for watcher in watchers
            )
        else:
            lines.append("(none; the fallback watcher checks every type)")
    if unrouted:
        lines.append(_SOURCE_ROUTING_ADVICE)
        lines.extend(f"- {use.source_type}{example(use.source_type)}" for use in unrouted)
    return "\n".join(lines)


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
    if "changes" in click:
        approved: list[dict[str, Any]] = click["changes"]
        if approved:
            lines.append("Approved changes; make these and no others:")
            lines.extend(f"  - {json.dumps(change, ensure_ascii=False)}" for change in approved)
        else:
            lines.append("Approved changes: none.")
    lines.append(
        "Interpret this click per the notice's intent and the Custodian manual. Read the "
        'notice itself with system_notice(action="get") if you need its full body.'
    )
    return "\n".join(lines)


def write_worker_message(item: AgentQueueItem) -> str:
    """Write a worker's message for a ``worker_message`` queue item: the text its sender wrote."""
    return item.payload["message"]


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


def _manager_events_text(events: Sequence[TaskEvent], matched: Mapping[str, Task]) -> str:
    """Write v2's manager notice lines, each with its event id and the broker's task match."""
    lines = [f"[System: {len(events)} event(s) for you — triage or act]"]
    by_session: dict[str, list[TaskEvent]] = {}
    for event in events:
        if is_session_event(event) and event.source_session_id:
            by_session.setdefault(event.source_session_id, []).append(event)
        else:
            lines.append(f"- [event:{event.id}] {_event_text(event)}")
            lines.extend(_broker_match_lines([event], matched))
    for session_events in by_session.values():
        lines.append(_session_entry(session_events))
        lines.extend(_broker_match_lines(session_events, matched))
    return "\n".join(lines)


def _broker_match_lines(events: Sequence[TaskEvent], matched: Mapping[str, Task]) -> list[str]:
    for event in events:
        if event.task_id is not None and event.task_id in matched:
            return [f"  Broker match: task {_task_line(matched[event.task_id])}"]
    return []


def _subscriber_events_text(events: Sequence[TaskEvent]) -> str:
    """Write the notice for a session that follows the events' sources."""
    lines = [f"[System: event — {len(events)} update(s) on sources you follow]"]
    for event in sorted(events, key=lambda event: (event.created_at, event.id)):
        if is_session_event(event):
            title = event.payload.get("session_title") or event.source_key
            lines.append(f"- {event.title} (session {title!r}, id {event.source_key})")
        else:
            lines.append(f"- {_event_text(event)}")
    return "\n".join(lines)


def _event_text(event: TaskEvent) -> str:
    """Write an event's type, source and title, and who reported it."""
    source = " ".join(part for part in (event.source, event.source_key) if part)
    text = (
        f"{event.event_type} {source}: {event.title!r}"
        if source
        else (f"{event.event_type}: {event.title!r}")
    )
    watcher = event.payload.get("watcher")
    if isinstance(watcher, dict) and watcher.get("name"):
        return f"{text} (reported by the watcher {watcher['name']!r})"
    if event.source_session_id:
        return f"{text} (reported by session {event.source_session_id})"
    return text


def _utc_day(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).strftime("%Y-%m-%d")


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
        return f"- {ids_label} {_event_text(first)}"
    return f"- {ids_label} {len(events)} events for session '{title}' (routed)"


def _task_line(task: Task) -> str:
    return f"{task.id} — {task.title} ({task.state})"


def _note_by_source(followed: Sequence[Subscription]) -> dict[tuple[str, str], str]:
    """Return the note of each followed source: that of its oldest subscription with one."""
    notes: dict[tuple[str, str], str] = {}
    for subscription in followed:
        note = " ".join((subscription.note or "").split())
        if note:
            notes.setdefault((subscription.source_type, subscription.source_key), note)
    return notes


def _with_note(line: str, note: str | None) -> str:
    return f"{line}\n  Note: {note}" if note else line


def _watch_item_line(item: WatchItem) -> str:
    title = f' "{item.title}"' if item.title else ""
    offsets = ", ".join(f"{name}={value}" for name, value in item.offsets.items())
    checked = datetime.fromtimestamp(item.checked_at, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"- {item.source} {item.source_key}{title}: {offsets or 'no offsets'} (checked {checked})"
    )
