"""Report the finished turns, deletions and failed starts of GlobalHub users' sessions as events.

A session is an ``omnigent.session`` source whose key is its id. Each session
that follows it, e.g. the manager of each task it works on, gets a report. A
session that no session follows reports its turns to the broker, whose chosen
manager follows it once the manager makes it a worker: always for a worker,
e.g. of a task without a manager, and for other sessions only when the owner
turned on reports of new sessions. Its deletion goes to whoever holds its waiting turn report. Role
sessions, sub-agents and side chats do not report, and a user without a
GlobalHub host gets no turn reports. A worker's finished turn also counts as
work on its tasks, which moves an active or idle task to the top of the board.
A session started for a task reports when its first message could not be sent.
A deleted session's subscriptions, and the subscriptions to it, go with it. A
clone that its source's owner makes follows the sources that its source follows;
a side chat and a continuation do not.
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
from omnigent.entities import SESSION_EVENT_SOURCE, Conversation, EventTag, TaskEvent
from omnigent.server.agent_queue_packagers import (
    SESSION_DELETED_EVENT_TYPE,
    SESSION_START_FAILED_EVENT_TYPE,
    SESSION_TURN_FINISHED_EVENT_TYPE,
)

_logger = logging.getLogger(__name__)

# A report waits until the broker or the session it went to handles it.
_WAITING_STATES = ("unrouted", "routed")
_START_ERROR_MAX_CHARS = 1000
# Where a report goes: the session that follows the source. The broker gets one with none.
_Target = str | None
_BROKER: _Target = None


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
        :param board: Holds the events, workers, tasks and subscriptions.
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
        workers = self._board.workers.list_for_session(session_id, owner_user_id=owner)
        for worker in workers:
            self._board.tasks.record_work(worker.task_id, owner_user_id=owner)
        settings = self._sessions.user_settings_store
        if settings.get_globalhub_placement(user_id=owner) is None:
            return
        waiting = self._waiting(SESSION_TURN_FINISHED_EVENT_TYPE, session_id, owner)
        followers = self._followers(session_id, owner)
        if followers:
            told = {event.target_session_id for event in waiting}
            targets: list[_Target] = [target for target in followers if target not in told]
        elif not workers and not settings.get_globalhub_report_new_sessions(user_id=owner):
            return
        else:
            # A report that still waits covers this turn too.
            targets = [] if waiting else [_BROKER]
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
        self._board.subscriptions.delete_for_session(conv.id, owner_user_id=owner)
        if not reports_to_globalhub(conv):
            return
        holders: list[_Target] = list(self._followers(conv.id, owner))
        for worker in self._board.workers.list_for_session(conv.id, owner_user_id=owner):
            self._board.workers.delete(worker.id, owner_user_id=owner)
        if not holders:
            # A session that no session follows tells whoever holds its waiting turn report.
            turns = self._waiting(SESSION_TURN_FINISHED_EVENT_TYPE, conv.id, owner)
            holders = list(dict.fromkeys(event.target_session_id for event in turns))
        deleted = self._waiting(SESSION_DELETED_EVENT_TYPE, conv.id, owner)
        told = {event.target_session_id for event in deleted}
        self._report(
            conv,
            owner,
            event_type=SESSION_DELETED_EVENT_TYPE,
            summary="Session deleted",
            targets=[holder for holder in holders if holder not in told],
        )
        self._board.subscriptions.delete_for_keys([conv.id], owner_user_id=owner)

    def copy_subscriptions(self, source_id: str, fork_id: str) -> None:
        """Make a fork follow the sources that its source session follows. Blocking."""
        owner = self._sessions.conversation_store.get_session_owner(source_id, owner_only=True)
        self._board.subscriptions.copy_session(source_id, fork_id, owner_user_id=owner)

    def report_start_failed(self, session_id: str, error: str) -> None:
        """Record that a started session did not get its first message. Blocking.

        The sessions that follow it hear of it; without one, the broker does.
        """
        conversations = self._sessions.conversation_store
        conv = conversations.get_conversation(session_id)
        if conv is None:
            return
        owner = conversations.get_session_owner(session_id, owner_only=True)
        self._report(
            conv,
            owner,
            event_type=SESSION_START_FAILED_EVENT_TYPE,
            summary="Session did not start",
            targets=list(self._followers(session_id, owner)) or [_BROKER],
            details={"error": error[:_START_ERROR_MAX_CHARS]},
        )

    def _waiting(self, event_type: str, session_id: str, owner: str | None) -> list[TaskEvent]:
        """The session's reports of ``event_type`` that no one has handled yet."""
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
        targets: Sequence[_Target],
        details: dict[str, str] | None = None,
    ) -> None:
        """Record one event for each target: a session that follows the source, or the broker."""
        title = conv.title or conv.id
        payload = {
            "session_id": conv.id,
            "session_title": title,
            "project_name": self._project_name(conv, owner),
            **(details or {}),
        }
        for target in targets:
            self._board.events.create(
                uuid.uuid4().hex,
                owner_user_id=owner,
                event_type=event_type,
                title=f"{summary}: {title}",
                payload=payload,
                source=SESSION_EVENT_SOURCE,
                source_key=conv.id,
                source_session_id=conv.id,
                tags=_host_tags(conv) if target is None else (),
                target_session_id=target,
            )

    def _followers(self, session_id: str, owner: str | None) -> list[str]:
        """Return the sessions that follow the session, other than itself."""
        return [
            follower
            for follower in self._board.subscriptions.subscribers(
                owner_user_id=owner, source_key=session_id
            )
            if follower != session_id
        ]

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


# The app's reporter, for sessions that main's routes delete or fork.
_installed: SessionEventReporter | None = None


def install_session_event_reporter(reporter: SessionEventReporter | None) -> None:
    """Make ``reporter`` the one that the session hooks use; ``None`` uninstalls it."""
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


async def after_session_fork(source_id: str, fork_id: str) -> None:
    """Make a fork follow its source's sources; a failure is logged, not raised."""
    if _installed is None:
        return
    try:
        await asyncio.to_thread(_installed.copy_subscriptions, source_id, fork_id)
    except Exception:
        _logger.exception(
            "GlobalHub could not copy the subscriptions of session %s to its fork %s",
            source_id,
            fork_id,
        )
