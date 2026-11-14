"""Send a message from the server into a GlobalHub role session that is not mid-turn.

Uses the path a scheduled task takes into an existing session: wake the
session's runner, then dispatch the message, which attaches the role manual
and stores the message before forwarding it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from omnigent.entities import Conversation
from omnigent.mcp_selection import mcp_selection_revision
from omnigent.server.schemas import SessionEventInput

_logger = logging.getLogger(__name__)

SendOutcome = Literal["sent", "busy", "unreachable", "gone", "failed"]


@dataclass(frozen=True)
class SessionSendDeps:
    """The server dependencies a send needs, captured from ``app.state`` once."""

    conversation_store: Any
    agent_store: Any
    app_state: Any
    runner_router: Any | None = None
    file_store: Any | None = None
    artifact_store: Any | None = None


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
    if _mid_turn(conv):
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
        if _mid_turn(conv):
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


def _mid_turn(conv: Conversation) -> bool:
    from omnigent.server.routes import sessions

    status = sessions._session_status_cache.get(conv.id, conv.live_status)
    return status in sessions._MID_TURN_STATUSES
