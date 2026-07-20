"""Protocols for host-side ambient source pollers."""

from __future__ import annotations

from typing import Protocol

from omnigent.host.polling.context import PollContext


class PollSource(Protocol):
    """One external source watched by the host poll scheduler."""

    @property
    def name(self) -> str:
        """Stable identifier used for logging and scheduler stats."""

    def enabled(self, ctx: PollContext) -> bool:
        """Return whether this poller should run."""

    def interval_s(self, ctx: PollContext) -> float:
        """Seconds to wait between successful poll cycles."""

    @property
    def read_only(self) -> bool:
        """When ``True``, the poller only mirrors external sessions into Omnigent."""

    async def on_start(self, ctx: PollContext) -> None:
        """Hydrate state before the first poll."""

    async def poll_once(self, ctx: PollContext) -> None:
        """Scan the source once and push updates to the server."""

    async def on_stop(self) -> None:
        """Release resources when the scheduler stops."""
