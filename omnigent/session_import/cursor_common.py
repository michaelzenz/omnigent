"""Shared helpers for Cursor CLI and IDE session import."""

from __future__ import annotations

import re

from omnigent.ambient_codex import AMBIENT_IMPORT_MAX_AGE_MS, thread_id_from_external_session_id
from omnigent.cursor_native_bridge import FORK_HISTORY_CLOSE_TAG, FORK_HISTORY_OPEN_TAG

_RESPONSE_ID_MAX_LEN = 64

_USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)
_ATTACHMENT_MARKER_RE = re.compile(r"\[Attached:[^\]]*\]")
_FORK_HISTORY_RE = re.compile(
    rf"{re.escape(FORK_HISTORY_OPEN_TAG)}.*?{re.escape(FORK_HISTORY_CLOSE_TAG)}"
    rf"|{re.escape(FORK_HISTORY_OPEN_TAG)}.*",
    re.DOTALL,
)
_COMPACTION_SUMMARY_PREFIX = "[Previous conversation summary]:"


def session_key_from_external_session_id(external_session_id: str) -> str | None:
    """Extract the Cursor session UUID from an import external session id."""
    return thread_id_from_external_session_id(external_session_id)


def cursor_response_id(blob_id: str) -> str:
    """Build a server-safe response id for one Cursor message."""
    return f"cursor:{blob_id}"[:_RESPONSE_ID_MAX_LEN]


def content_text(content: object) -> str:
    """Join the ``text`` of a cursor message's content (str or part list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ]
        return "".join(parts)
    return ""


def strip_control_chars(text: str) -> str:
    """Drop C0 control bytes cursor embeds in stored prompts (keep \\n and \\t)."""
    return "".join(ch for ch in text if ch >= " " or ch in "\n\t")


def unwrap_user_query(text: str) -> str | None:
    """Return the human prompt from a stored user blob, or ``None`` to skip it."""
    match = _USER_QUERY_RE.search(text)
    if match is None:
        return None
    inner = _FORK_HISTORY_RE.sub("", strip_control_chars(match.group(1)))
    inner = _ATTACHMENT_MARKER_RE.sub("", inner)
    return inner.strip() or None


def is_recent_mtime_ms(mtime_ms: int, *, now_ms: int | None = None) -> bool:
    """Return whether *mtime_ms* falls within the ambient import window."""
    import time

    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return mtime_ms >= current_ms - AMBIENT_IMPORT_MAX_AGE_MS


__all__ = [
    "AMBIENT_IMPORT_MAX_AGE_MS",
    "content_text",
    "cursor_response_id",
    "is_recent_mtime_ms",
    "session_key_from_external_session_id",
    "unwrap_user_query",
    "_COMPACTION_SUMMARY_PREFIX",
]
