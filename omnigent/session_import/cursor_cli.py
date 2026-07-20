"""Read cursor-agent CLI chat stores for ambient import."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from omnigent.entities import MessageData, NewConversationItem
from omnigent.session_import.cursor_common import (
    _COMPACTION_SUMMARY_PREFIX,
    content_text,
    cursor_response_id,
    is_recent_mtime_ms,
    unwrap_user_query,
)
from omnigent.session_import.models import ImportSource, LocalSessionImport

_AGENT_NAME = "cursor-native-ui"


@dataclass(frozen=True)
class CursorCliChat:
    """One cursor-agent CLI chat discovered on disk."""

    chat_id: str
    store_path: Path
    mtime_ms: int


@dataclass(frozen=True)
class CursorCliReadResult:
    """Incremental read from one cursor CLI store."""

    items: tuple[NewConversationItem, ...]
    last_rowid: int


def default_cursor_chats_root() -> Path:
    """Return ``~/.cursor/chats`` for ambient discovery."""
    return Path.home() / ".cursor" / "chats"


def workspace_hash(workspace: str) -> str:
    """Return cursor's chat-dir key for *workspace* (``md5`` of the path)."""
    return hashlib.md5(workspace.encode("utf-8")).hexdigest()


def cursor_project_slug(workspace: str) -> str:
    """Return cursor's project slug for an IDE workspace path."""
    normalized = Path(workspace).expanduser().resolve().as_posix().lstrip("/")
    return normalized.replace("/", "-")


def _chat_created_ms(chat_dir: Path) -> int:
    try:
        meta = json.loads((chat_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    created = meta.get("createdAtMs")
    return created if isinstance(created, int) else 0


def _chat_mtime_ms(store_path: Path) -> int:
    created = _chat_created_ms(store_path.parent)
    try:
        store_mtime_ms = int(store_path.stat().st_mtime * 1000)
    except OSError:
        store_mtime_ms = 0
    return max(created, store_mtime_ms)


def iter_cursor_cli_chats(chats_root: Path | None = None) -> Iterator[CursorCliChat]:
    """Yield CLI chat stores under ``~/.cursor/chats``, newest first."""
    root = default_cursor_chats_root() if chats_root is None else chats_root
    if not root.is_dir():
        return
    matches: list[CursorCliChat] = []
    for hash_dir in root.iterdir():
        if not hash_dir.is_dir():
            continue
        for chat_dir in hash_dir.iterdir():
            store_path = chat_dir / "store.db"
            if not store_path.is_file():
                continue
            mtime_ms = _chat_mtime_ms(store_path)
            if not is_recent_mtime_ms(mtime_ms):
                continue
            matches.append(
                CursorCliChat(
                    chat_id=chat_dir.name,
                    store_path=store_path,
                    mtime_ms=mtime_ms,
                )
            )
    matches.sort(key=lambda entry: entry.mtime_ms, reverse=True)
    yield from matches


def _read_blob_rows(store_path: Path, last_rowid: int) -> list[tuple[int, str, object]]:
    sql = "SELECT rowid, id, data FROM blobs WHERE rowid > ? ORDER BY rowid"
    for uri, kw in ((f"file:{store_path}?mode=ro", {"uri": True}), (str(store_path), {})):
        try:
            con = sqlite3.connect(uri, timeout=5.0, **kw)
        except sqlite3.Error:
            continue
        try:
            return con.execute(sql, (last_rowid,)).fetchall()
        except sqlite3.Error:
            continue
        finally:
            con.close()
    return []


def _max_rowid(store_path: Path) -> int:
    sql = "SELECT MAX(rowid) FROM blobs"
    for uri, kw in ((f"file:{store_path}?mode=ro", {"uri": True}), (str(store_path), {})):
        try:
            con = sqlite3.connect(uri, timeout=5.0, **kw)
        except sqlite3.Error:
            continue
        try:
            row = con.execute(sql).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except sqlite3.Error:
            continue
        finally:
            con.close()
    return 0


def _blob_to_item(rowid: int, blob_id: str, data: object) -> NewConversationItem | None:
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(data, str):
        return None
    try:
        obj = json.loads(data)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    role = obj.get("role")
    response_id = cursor_response_id(blob_id)
    if role == "user":
        message_content = obj.get("content")
        if isinstance(message_content, str) and message_content.startswith(
            _COMPACTION_SUMMARY_PREFIX
        ):
            return None
        prompt = unwrap_user_query(content_text(message_content))
        if not prompt:
            return None
        return NewConversationItem(
            type="message",
            response_id=response_id,
            data=MessageData(
                role="user",
                content=[{"type": "input_text", "text": prompt}],
            ),
        )
    if role == "assistant":
        text = content_text(obj.get("content")).strip()
        if not text:
            return None
        return NewConversationItem(
            type="message",
            response_id=response_id,
            data=MessageData(
                role="assistant",
                agent=_AGENT_NAME,
                content=[{"type": "output_text", "text": text}],
            ),
        )
    return None


def read_cursor_cli_from_rowid(store_path: Path, *, last_rowid: int) -> CursorCliReadResult:
    """Read new conversation items from one CLI store."""
    items: list[NewConversationItem] = []
    high_water = last_rowid
    for rowid, blob_id, data in _read_blob_rows(store_path, last_rowid):
        high_water = max(high_water, rowid)
        item = _blob_to_item(rowid, blob_id, data)
        if item is not None:
            items.append(item)
    return CursorCliReadResult(items=tuple(items), last_rowid=high_water)


def load_cursor_cli_session(store_path: Path) -> LocalSessionImport:
    """Load one CLI chat store as a normalized import payload."""
    read_result = read_cursor_cli_from_rowid(store_path, last_rowid=0)
    if not read_result.items:
        from omnigent.session_import.models import SessionImportNotFoundError

        raise SessionImportNotFoundError(f"cursor CLI chat has no messages: {store_path}")
    source: ImportSource = "cursor-cli"
    return LocalSessionImport(
        source=source,
        external_session_id=store_path.parent.name,
        workspace=None,
        items=read_result.items,
    )


def initial_cursor_cli_rowid(store_path: Path) -> int:
    """Return the rowid high-water mark after importing full history."""
    return _max_rowid(store_path)
