"""Inject a user message into a session when a prompt timer fires."""

from __future__ import annotations

import logging
from typing import Any

from omnigent.host.timer.context import TimerContext

_logger = logging.getLogger(__name__)


class PromptTimerHandler:
    """Deliver ``payload.message`` into ``payload.session_id``."""

    async def handle(self, ctx: TimerContext, *, item_id: str, payload: dict[str, Any]) -> None:
        session_id = payload.get("session_id")
        message = payload.get("message")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("prompt timer payload requires non-empty session_id")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("prompt timer payload requires non-empty message")
        response = await ctx.client.post(
            "/v1/timer-items/dispatch-prompt",
            json={"session_id": session_id.strip(), "message": message},
        )
        response.raise_for_status()
        delivered = response.json().get("delivered")
        if delivered is not True:
            _logger.warning(
                "prompt timer %s: dispatch-prompt returned delivered=%s for session %s",
                item_id,
                delivered,
                session_id,
            )
