"""Protocols for host-side timer handlers."""

from __future__ import annotations

from typing import Any, Protocol

from omnigent.host.timer.context import TimerContext


class TimerHandler(Protocol):
    """Execute one claimed timer item."""

    async def handle(self, ctx: TimerContext, *, item_id: str, payload: dict[str, Any]) -> None:
        """Run the handler for one timer item."""
