"""Read Cursor IDE agent transcripts for ambient import."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from omnigent.claude_native_bridge import _read_complete_jsonl_records
from omnigent.entities import MessageData, NewConversationItem
from omnigent.session_import.cursor_common import (
    content_text,
    cursor_response_id,
    is_recent_mtime_ms,
    unwrap_user_query,
)
from omnigent.session_import.models import ImportSource, LocalSessionImport

_AGENT_NAME = "cursor-native-ui"


@dataclass(frozen=True)
class CursorIdeTranscript:
    """One Cursor IDE agent transcript discovered on disk."""

    transcript_id: str
    transcript_path: Path
    workspace: str | None
    mtime_ms: int


@dataclass(frozen=True)
class CursorIdeReadResult:
    """Incremental read from one IDE transcript JSONL."""

    items: tuple[NewConversationItem, ...]
    byte_offset: int


def default_cursor_projects_root() -> Path:
    """Return ``~/.cursor/projects`` for ambient discovery."""
    return Path.home() / ".cursor" / "projects"


def _slug_to_workspace(slug: str) -> str | None:
    if not slug:
        return None
    return "/" + slug.replace("-", "/")


def _transcript_mtime_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def iter_cursor_ide_transcripts(
    projects_root: Path | None = None,
) -> Iterator[CursorIdeTranscript]:
    """Yield IDE transcript JSONL files, newest first."""
    root = default_cursor_projects_root() if projects_root is None else projects_root
    if not root.is_dir():
        return
    matches: list[CursorIdeTranscript] = []
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        transcripts_dir = project_dir / "agent-transcripts"
        if not transcripts_dir.is_dir():
            continue
        workspace = _slug_to_workspace(project_dir.name)
        for transcript_dir in transcripts_dir.iterdir():
            if not transcript_dir.is_dir():
                continue
            jsonl_path = transcript_dir / f"{transcript_dir.name}.jsonl"
            if not jsonl_path.is_file():
                continue
            mtime_ms = _transcript_mtime_ms(jsonl_path)
            if not is_recent_mtime_ms(mtime_ms):
                continue
            matches.append(
                CursorIdeTranscript(
                    transcript_id=transcript_dir.name,
                    transcript_path=jsonl_path,
                    workspace=workspace,
                    mtime_ms=mtime_ms,
                )
            )
    matches.sort(key=lambda entry: entry.mtime_ms, reverse=True)
    yield from matches


def _record_to_item(record: dict[str, object], *, index: int) -> NewConversationItem | None:
    role = record.get("role")
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    response_id = cursor_response_id(f"ide:{index}")
    if role == "user":
        prompt = unwrap_user_query(content_text(message.get("content")))
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
        text = content_text(message.get("content")).strip()
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


def read_cursor_ide_from_offset(
    transcript_path: Path,
    *,
    byte_offset: int,
) -> CursorIdeReadResult:
    """Read new conversation items from one IDE transcript."""
    read_result = _read_complete_jsonl_records(
        transcript_path,
        byte_offset=byte_offset,
        start_line=0,
    )
    items: list[NewConversationItem] = []
    for index, record in enumerate(read_result.records):
        if record.text is None:
            continue
        try:
            entry = json.loads(record.text)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        item = _record_to_item(entry, index=index)
        if item is not None:
            items.append(item)
    return CursorIdeReadResult(items=tuple(items), byte_offset=read_result.byte_offset)


def load_cursor_ide_session(transcript_path: Path, *, workspace: str | None = None) -> LocalSessionImport:
    """Load one IDE transcript as a normalized import payload."""
    read_result = read_cursor_ide_from_offset(transcript_path, byte_offset=0)
    if not read_result.items:
        from omnigent.session_import.models import SessionImportNotFoundError

        raise SessionImportNotFoundError(f"cursor IDE transcript has no messages: {transcript_path}")
    source: ImportSource = "cursor-ide"
    return LocalSessionImport(
        source=source,
        external_session_id=transcript_path.parent.name,
        workspace=workspace,
        items=read_result.items,
    )


def initial_cursor_ide_byte_offset(transcript_path: Path) -> int:
    """Return the byte offset high-water mark after importing full history."""
    try:
        return transcript_path.stat().st_size
    except OSError:
        return 0
