"""Shared types for Codex ambient bridge sync."""

from __future__ import annotations

import re
from dataclasses import dataclass

HOST_AMBIENT_ID_HEADER = "X-Omnigent-Host-Id"

# Rollouts not touched within this window are not auto-imported on discovery.
AMBIENT_IMPORT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

_THREAD_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AmbientCodexCursor:
    """Poll cursor for one imported Codex session."""

    byte_offset: int
    turn_id: str
    rollout_path: str
    connection_id: str | None = None


@dataclass(frozen=True)
class AmbientCodexTrack:
    """One Codex session tracked by an ambient host poller."""

    session_id: str
    external_session_id: str
    thread_id: str
    byte_offset: int
    turn_id: str
    rollout_path: str
    connection_id: str | None
    workspace: str | None = None


def thread_id_from_external_session_id(external_session_id: str) -> str | None:
    """Extract the Codex thread UUID from an import external session id."""
    match = _THREAD_ID_RE.search(external_session_id.strip())
    return match.group(1) if match is not None else None


__all__ = [
    "AMBIENT_IMPORT_MAX_AGE_MS",
    "AmbientCodexCursor",
    "AmbientCodexTrack",
    "HOST_AMBIENT_ID_HEADER",
    "thread_id_from_external_session_id",
]
