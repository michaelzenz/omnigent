"""Send a message from the server into a GlobalHub role session that is not mid-turn.

Uses the path a scheduled task takes into an existing session: wake the
session's runner, then dispatch the message, which attaches the role manual
and stores the message before forwarding it. A session whose turn lost its
runner is settled the way main's session list settles it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from omnigent.entities import Conversation
from omnigent.mcp_selection import mcp_selection_revision
from omnigent.server.schemas import SessionEventInput
from omnigent.stores.conversation_store import RUNNER_LIVENESS_TTL_S, runner_seen_is_fresh

if TYPE_CHECKING:
    from omnigent.server.routes._sessions.helpers import SessionLiveness

_logger = logging.getLogger(__name__)

SendOutcome = Literal["sent", "busy", "unreachable", "gone", "failed"]


class TurnEndListener(Protocol):
    """Told when a session's turn ends, e.g. the GlobalHub queue's dispatcher."""

    def turn_ended(self, session_id: str, status: str) -> None:
        """Handle a turn that ended ``"idle"`` or ``"failed"``, without blocking the loop."""


_installed: tuple[TurnEndListener, ...] = ()


def install_turn_listeners(*listeners: TurnEndListener) -> None:
    """Make ``listeners`` the ones :func:`notify_turn_end` tells; none uninstalls them."""
    global _installed
    _installed = listeners


def notify_turn_end(session_id: str, status: str) -> None:
    """Tell each installed listener that a session's turn ended (``"idle"``) or failed."""
    for listener in _installed:
        try:
            listener.turn_ended(session_id, status)
        except Exception:
            # A GlobalHub listener must not break main's status publishing.
            _logger.exception("GlobalHub turn listener %r failed for %s", listener, session_id)


@dataclass(frozen=True)
class SessionSendDeps:
    """The server dependencies for reaching a session's runner, taken from ``app.state`` once."""

    conversation_store: Any
    agent_store: Any
    app_state: Any
    runner_router: Any | None = None
    file_store: Any | None = None
    artifact_store: Any | None = None
    # Main's bulk session liveness lookup; without it, no orphaned turn is settled.
    liveness_lookup: Callable[[list[str]], Mapping[str, SessionLiveness]] | None = None


@dataclass(frozen=True)
class SendResult:
    """How a send ended.

    :param outcome: ``"sent"``; ``"busy"`` while a turn runs; ``"unreachable"``
        when no runner can be reached or woken; ``"gone"`` when the session is
        missing or archived; ``"failed"`` when the runner did not take it.
    :param error: Why the send did not succeed, for the queue item.
    """

    outcome: SendOutcome
    error: str | None = None


async def send_to_session(
    session_id: str, text: str, *, created_by: str | None, deps: SessionSendDeps
) -> SendResult:
    """
    Send ``text`` as a user message into a session that is not mid-turn.

    :param session_id: The session to message.
    :param text: The message.
    :param created_by: The message's author, the session's owner.
    :param deps: Where the session lives and how its runner is reached.
    :returns: The outcome; only ``"sent"`` means the runner took the message.
    """
    from omnigent.server.routes import sessions

    conv = await asyncio.to_thread(deps.conversation_store.get_conversation, session_id)
    if conv is None or conv.archived:
        return SendResult("gone")
    if await session_has_live_turn(conv, deps=deps):
        return SendResult("busy")
    try:
        runner_client, conv = await sessions.ensure_runner_connected(
            session_id=conv.id,
            conv=conv,
            app_state=deps.app_state,
            conversation_store=deps.conversation_store,
            runner_router=deps.runner_router,
        )
        if runner_client is None:
            return SendResult("unreachable", "no runner can be reached or woken")
        # Waking the runner takes time; the user may have started a turn meanwhile.
        if session_mid_turn(conv):
            return SendResult("busy")
        await sessions._ensure_runner_session_initialized(
            conv.id, conv, runner_client, deps.conversation_store
        )
        agent = (
            await asyncio.to_thread(deps.agent_store.get, conv.agent_id) if conv.agent_id else None
        )
        result = await sessions._dispatch_session_event_to_runner(
            conv.id,
            conv,
            SessionEventInput(
                type="message",
                data={"role": "user", "content": [{"type": "input_text", "text": text}]},
            ),
            deps.conversation_store,
            runner_client,
            agent_name=None,
            file_store=deps.file_store,
            artifact_store=deps.artifact_store,
            created_by=created_by,
            runner_router=deps.runner_router,
            agent_revision=(
                mcp_selection_revision(agent.bundle_location, conv.labels) if agent else None
            ),
        )
    except Exception as exc:
        _logger.warning("GlobalHub send to session %s failed", session_id, exc_info=True)
        return SendResult("failed", str(exc) or type(exc).__name__)
    if not result.accepted:
        return SendResult("failed", "the runner did not accept the message")
    return SendResult("sent")


def session_mid_turn(conv: Conversation) -> bool:
    """Whether a turn is running in the session, by main's live status."""
    from omnigent.server.routes import sessions

    status = sessions._session_status_cache.get(conv.id, conv.live_status)
    return status in sessions._MID_TURN_STATUSES


async def settle_orphaned_turns(
    conversations: Iterable[Conversation], *, deps: SessionSendDeps
) -> list[str]:
    """
    Settle to idle each session that reads mid-turn although its runner is gone.

    When the server and the runner stop together, nothing reports the end of
    the turn. This runs the check main's session list runs, which also waits
    for an offline host that can still resume the turn.

    :param conversations: The sessions to check.
    :param deps: Where the sessions live and how runner liveness is read.
    :returns: The ids of the sessions this call settled.
    """
    from omnigent.server.routes import sessions
    from omnigent.server.routes._sessions.helpers import reconcile_orphaned_running_status

    if deps.liveness_lookup is None:
        return []
    suspects = [
        conv
        for conv in conversations
        if conv.agent_id is not None
        and conv.runner_id is not None
        and conv.live_status in sessions._MID_TURN_STATUSES
        and sessions._session_status_cache.get(conv.id) is None
        and not runner_seen_is_fresh(conv.runner_last_seen)
    ]
    if not suspects:
        return []
    liveness = await asyncio.to_thread(deps.liveness_lookup, [conv.id for conv in suspects])
    now = int(time.time())
    hold_stale_before = now - sessions.HOST_OFFLINE_HOLD_S - 2 * RUNNER_LIVENESS_TTL_S
    host_store = getattr(deps.app_state, "host_store", None)
    offline_hosts: dict[str, bool | None] = {}
    settled: list[str] = []
    for conv in suspects:
        result = liveness.get(conv.id)
        if result is None or result.runner_online:
            continue
        if result.host_online is False and conv.host_id is not None:
            if conv.host_id not in offline_hosts:
                offline_hosts[conv.host_id] = await sessions._host_row_offline(
                    host_store, conv.host_id, updated_since=hold_stale_before
                )
            if offline_hosts[conv.host_id] is not False:
                continue
        if await asyncio.to_thread(
            reconcile_orphaned_running_status,
            conv.id,
            deps.conversation_store,
            now - RUNNER_LIVENESS_TTL_S,
        ):
            settled.append(conv.id)
    return settled


async def session_has_live_turn(conv: Conversation, *, deps: SessionSendDeps) -> bool:
    """Whether a turn runs in the session, once a turn whose runner is gone is settled."""
    return session_mid_turn(conv) and not await settle_orphaned_turns([conv], deps=deps)
