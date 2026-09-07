"""In-memory lifecycle log ring for SSH connections, shown in the settings UI.

The host daemon pushes entries as it reconciles each connection; the ring
server keeps the tail so ``GET /v1/ssh/connections/{id}/logs`` renders the
same timeline it did when the server executed SSH itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from omnigent.db.utils import now_epoch

_MAX_LOG_ENTRIES = 200


@dataclass(frozen=True)
class SshHostLogEntry:
    """One captured installation lifecycle event for the settings UI."""

    timestamp: int
    phase: str
    level: str
    message: str


class SshLogRing:
    """Bounded per-connection log tail."""

    def __init__(self, *, max_entries: int = _MAX_LOG_ENTRIES) -> None:
        self._max_entries = max_entries
        self._logs: dict[str, list[SshHostLogEntry]] = {}

    def append(
        self,
        connection_id: str,
        *,
        phase: str,
        level: str,
        message: str,
    ) -> None:
        entries = self._logs.setdefault(connection_id, [])
        entries.append(
            SshHostLogEntry(
                timestamp=now_epoch(),
                phase=phase,
                level=level,
                message=message[:4000],
            )
        )
        if len(entries) > self._max_entries:
            del entries[: len(entries) - self._max_entries]

    def entries(self, connection_id: str) -> list[SshHostLogEntry]:
        """Return captured lifecycle events for a connection (newest last)."""
        return list(self._logs.get(connection_id, []))
