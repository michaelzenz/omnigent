"""Report the finished turns and deletions of each GlobalHub user's sessions as task events.

A session that works on live tasks reports to those tasks' managers. Any other
session reports its turns to the broker, whose chosen manager may attach it to
a task, and its deletion to whoever holds its waiting turn report. Role
sessions, sub-agents and side chats do not report, and a user without a
GlobalHub host gets no turn reports.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.event_batches import HOST_TAG_TYPE
from omnigent.agent_tasks.role_sessions import RoleSessionStores
from omnigent.agent_tasks.session_labels import ROLE_LABEL
from omnigent.entities import LIVE_TASK_STATES, Conversation, EventTag, TaskEvent
from omnigent.server.agent_queue_packagers import (
    SESSION_DELETED_EVENT_TYPE,
    SESSION_TURN_FINISHED_EVENT_TYPE,
)

_logger = logging.getLogger(__name__)

SESSION_EVENT_SOURCE = "session"
# A report waits until the broker or a manager handles it.
_WAITING_STATES = ("unrouted", "routed")


def reports_to_globalhub(conv: Conversation) -> bool:
    """Whether a session reports to GlobalHub: not a role session, sub-agent or side chat."""
    return (
        ROLE_LABEL not in conv.labels
        and conv.kind != "sub_agent"
        and conv.side_chat_parent_id is None
    )


class SessionEventReporter:
    """Records the task events for the finished turns and deletions of users' sessions."""

    def __init__(self, *, board: BoardStores, sessions: RoleSessionStores) -> None:
        """
        :param board: Holds the events, workers, tasks and managers.
        :param sessions: Holds the sessions, their owners' GlobalHub hosts and projects.
        """
        self._board = board
        self._sessions = sessions
        self._reports: set[asyncio.Task[None]] = set()

    def turn_ended(self, session_id: str, status: str) -> None:
        """Report a finished turn in the background; a no-op outside an event loop."""
        if status != "idle":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        report = loop.create_task(asyncio.to_thread(self.report_turn_finished, session_id))
        self._reports.add(report)
        report.add_done_callback(self._forget)

    async def stop(self) -> None:
        """Wait for the reports in progress."""
        await asyncio.gather(*self._reports, return_exceptions=True)

    def report_turn_finished(self, session_id: str) -> None:
        """Record the events for a session's finished turn. Blocking."""
        conversations = self._sessions.conversation_store
        conv = conversations.get_conversation(session_id)
        if conv is None or not reports_to_globalhub(conv):
            return
        owner = conversations.get_session_owner(session_id, owner_only=True)
        if self._sessions.user_settings_store.get_globalhub_placement(user_id=owner) is None:
            return
        waiting = self._waiting(SESSION_TURN_FINISHED_EVENT_TYPE, session_id, owner)
        managers = self._managers_of_live_tasks(session_id, owner)
        if managers:
            told = {event.manager_id for event in waiting}
            targets: list[str | None] = [manager for manager in managers if manager not in told]
        else:
            # A report that still waits covers this turn too.
            targets = [] if waiting else [None]
        self._report(
            conv,
            owner,
            event_type=SESSION_TURN_FINISHED_EVENT_TYPE,
            summary="Session turn finished",
            targets=targets,
            details={"status": "idle"},
        )

    def report_session_deleted(self, conv: Conversation) -> None:
        """Record that a session goes away; call it while the owner's grant exists. Blocking."""
        owner = self._sessions.conversation_store.get_session_owner(conv.id, owner_only=True)
        for item in self._board.queue.list(owner_user_id=owner, target_session_id=conv.id):
            # A role session's messages go nowhere now; their events wait again.
            self._board.queue.delete(item.id, owner_user_id=owner)
        if not reports_to_globalhub(conv):
            return
        holders: Sequence[str | None] = self._managers_of_live_tasks(conv.id, owner)
        for worker in self._board.workers.list_for_session(conv.id, owner_user_id=owner):
            self._board.workers.delete(worker.id, owner_user_id=owner)
        if not holders:
            # A session on no live task tells whoever holds its waiting turn report.
            turns = self._waiting(SESSION_TURN_FINISHED_EVENT_TYPE, conv.id, owner)
            holders = list(dict.fromkeys(event.manager_id for event in turns))
        deleted = self._waiting(SESSION_DELETED_EVENT_TYPE, conv.id, owner)
        told = {event.manager_id for event in deleted}
        self._report(
            conv,
            owner,
            event_type=SESSION_DELETED_EVENT_TYPE,
            summary="Session deleted",
            targets=[holder for holder in holders if holder not in told],
        )

    def _waiting(self, event_type: str, session_id: str, owner: str | None) -> list[TaskEvent]:
        """The session's reports of ``event_type`` that the broker or a manager has not handled."""
        return self._board.events.list(
            owner_user_id=owner,
            states=_WAITING_STATES,
            event_type=event_type,
            source_key=session_id,
        )

    def _report(
        self,
        conv: Conversation,
        owner: str | None,
        *,
        event_type: str,
        summary: str,
        targets: Sequence[str | None],
        details: dict[str, str] | None = None,
    ) -> None:
        """Record one event for each target: a manager's id, or ``None`` for the broker."""
        title = conv.title or conv.id
        payload = {
            "session_id": conv.id,
            "session_title": title,
            "project_name": self._project_name(conv, owner),
            **(details or {}),
        }
        for manager_id in targets:
            self._board.events.create(
                uuid.uuid4().hex,
                owner_user_id=owner,
                event_type=event_type,
                title=f"{summary}: {title}",
                payload=payload,
                source=SESSION_EVENT_SOURCE,
                source_key=conv.id,
                source_session_id=conv.id,
                tags=_host_tags(conv) if manager_id is None else (),
                manager_id=manager_id,
            )

    def _managers_of_live_tasks(self, session_id: str, owner: str | None) -> list[str]:
        """Return the managers of the live tasks the session works on, each once."""
        managers: dict[str, None] = {}
        for worker in self._board.workers.list_for_session(session_id, owner_user_id=owner):
            task = self._board.tasks.get(worker.task_id, owner_user_id=owner)
            if task is None or task.state not in LIVE_TASK_STATES or task.manager_id is None:
                continue
            if self._board.managers.get(task.manager_id, owner_user_id=owner) is not None:
                managers.setdefault(task.manager_id)
        return list(managers)

    def _project_name(self, conv: Conversation, owner: str | None) -> str | None:
        projects = self._sessions.project_store
        if projects is None or conv.project_id is None:
            return None
        project = projects.get(conv.project_id, user_id=owner)
        return project.name if project is not None else None

    def _forget(self, report: asyncio.Task[None]) -> None:
        self._reports.discard(report)
        if not report.cancelled() and report.exception() is not None:
            _logger.error("GlobalHub session report failed", exc_info=report.exception())


def _host_tags(conv: Conversation) -> list[EventTag]:
    """The host tag the broker groups a session's report by."""
    return [EventTag(tag_type=HOST_TAG_TYPE, tag=conv.host_id)] if conv.host_id else []


# The app's reporter, for sessions that main's routes delete.
_installed: SessionEventReporter | None = None


def install_session_event_reporter(reporter: SessionEventReporter | None) -> None:
    """Make ``reporter`` the one :func:`before_session_delete` uses; ``None`` uninstalls it."""
    global _installed
    _installed = reporter


async def before_session_delete(conv: Conversation) -> None:
    """Report a session that is about to be deleted; a failure is logged, not raised."""
    if _installed is None:
        return
    try:
        await asyncio.to_thread(_installed.report_session_deleted, conv)
    except Exception:
        _logger.exception("GlobalHub could not report the deletion of session %s", conv.id)
