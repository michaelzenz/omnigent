"""Report the finished turns of each GlobalHub user's sessions as task events.

A session that works on live tasks reports to those tasks' managers. Any other
session reports to the broker, whose chosen manager may attach it to a task.
Role sessions, sub-agents and side chats do not report, and neither do the
sessions of a user without a GlobalHub host.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.event_batches import HOST_TAG_TYPE
from omnigent.agent_tasks.role_sessions import RoleSessionStores
from omnigent.agent_tasks.session_labels import ROLE_LABEL
from omnigent.entities import LIVE_TASK_STATES, Conversation, EventTag
from omnigent.server.agent_queue_packagers import SESSION_TURN_FINISHED_EVENT_TYPE

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
    """Records a task event for the finished turns of each GlobalHub user's sessions."""

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
        waiting = self._board.events.list(
            owner_user_id=owner,
            states=_WAITING_STATES,
            event_type=SESSION_TURN_FINISHED_EVENT_TYPE,
            source_key=session_id,
        )
        managers = self._managers_of_live_tasks(session_id, owner)
        if managers:
            told = {event.manager_id for event in waiting}
            targets: list[str | None] = [manager for manager in managers if manager not in told]
        else:
            # A report that still waits covers this turn too.
            targets = [] if waiting else [None]
        title = conv.title or session_id
        payload = {
            "session_id": session_id,
            "session_title": title,
            "project_name": self._project_name(conv, owner),
            "status": "idle",
        }
        for manager_id in targets:
            self._board.events.create(
                uuid.uuid4().hex,
                owner_user_id=owner,
                event_type=SESSION_TURN_FINISHED_EVENT_TYPE,
                title=f"Session turn finished: {title}",
                payload=payload,
                source=SESSION_EVENT_SOURCE,
                source_key=session_id,
                source_session_id=session_id,
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
