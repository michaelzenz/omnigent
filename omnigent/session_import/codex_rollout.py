"""Incremental reads of Codex rollout JSONL files for ambient sync."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from omnigent.claude_native_bridge import _read_complete_jsonl_records
from omnigent.codex_native import _find_codex_rollout
from omnigent.entities import NewConversationItem
from omnigent.session_import.local import _codex_response_item

_ROLLOUT_THREAD_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)


def active_codex_rollout_path(codex_home: Path, thread_id: str) -> Path | None:
    """Return the active Codex rollout path for a thread, if one exists."""
    return _find_codex_rollout(codex_home, thread_id)


def default_codex_home() -> Path:
    """Return the Codex home directory used for ambient discovery."""
    configured_home = os.environ.get("CODEX_HOME")
    if configured_home:
        return Path(configured_home).expanduser()
    return Path.home() / ".codex"


def thread_id_from_rollout_path(path: Path) -> str | None:
    """Extract a Codex thread id from a rollout filename."""
    match = _ROLLOUT_THREAD_ID_RE.search(path.name)
    if match is None:
        return None
    return match.group(1)


def iter_codex_rollout_paths(codex_home: Path) -> Iterator[Path]:
    """Yield rollout JSONL paths under one Codex home, newest first."""
    matches: list[Path] = []
    sessions = codex_home / "sessions"
    if sessions.is_dir():
        matches.extend(path for path in sessions.glob("**/rollout-*.jsonl") if path.is_file())
    archived = codex_home / "archived_sessions"
    if archived.is_dir():
        matches.extend(path for path in archived.glob("rollout-*.jsonl") if path.is_file())
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    yield from matches


@dataclass(frozen=True)
class CodexRolloutReadResult:
    """Items parsed from a rollout tail plus updated cursors."""

    items: tuple[NewConversationItem, ...]
    byte_offset: int
    turn_id: str
    workspace: str | None


def read_codex_rollout_from_offset(
    rollout_path: Path,
    *,
    byte_offset: int,
    turn_id: str = "history",
    workspace: str | None = None,
) -> CodexRolloutReadResult:
    """Read Codex response items appended after a rollout byte offset."""
    read_result = _read_complete_jsonl_records(
        rollout_path,
        byte_offset=byte_offset,
        start_line=0,
    )
    active_turn_id = turn_id
    active_workspace = workspace
    items: list[NewConversationItem] = []
    for record in read_result.records:
        if record.text is None:
            continue
        try:
            entry = json.loads(record.text)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
            continue
        payload = entry["payload"]
        if entry.get("type") == "session_meta":
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                active_workspace = cwd.strip()
            continue
        if entry.get("type") == "turn_context":
            candidate = payload.get("turn_id")
            if isinstance(candidate, str) and candidate:
                active_turn_id = candidate
            continue
        if entry.get("type") != "response_item":
            continue
        item = _codex_response_item(payload, response_id=f"codex:{active_turn_id}")
        if item is not None:
            items.append(item)
    return CodexRolloutReadResult(
        items=tuple(items),
        byte_offset=read_result.byte_offset,
        turn_id=active_turn_id,
        workspace=active_workspace,
    )


__all__ = [
    "CodexRolloutReadResult",
    "active_codex_rollout_path",
    "default_codex_home",
    "iter_codex_rollout_paths",
    "read_codex_rollout_from_offset",
    "thread_id_from_rollout_path",
]
