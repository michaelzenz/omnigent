"""Tests for Cursor ambient session import and polling."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from omnigent.ambient_codex import HOST_AMBIENT_ID_HEADER
from omnigent.host.polling.pollers.cursor_cli import CursorCliAmbientPoller
from omnigent.host.polling.pollers.cursor_cli_local import CursorCliLocalSubPoller
from omnigent.host.polling.pollers.ambient_state import AmbientBridgeState
from omnigent.host.polling.context import PollContext
from omnigent.session_import.cursor_cli import (
    initial_cursor_cli_rowid,
    iter_cursor_cli_chats,
    load_cursor_cli_session,
    read_cursor_cli_from_rowid,
    workspace_hash,
)
from omnigent.session_import.cursor_ide import (
    iter_cursor_ide_transcripts,
    load_cursor_ide_session,
    read_cursor_ide_from_offset,
)
from omnigent.session_import.models import import_conversation_id

_HOST_ID = "b" * 32


def _write_cli_store(root: Path, workspace: str, chat_id: str, user_text: str) -> Path:
    chat_dir = root / workspace_hash(workspace) / chat_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "meta.json").write_text(
        json.dumps({"createdAtMs": 9_999_999_999_000}),
        encoding="utf-8",
    )
    store_path = chat_dir / "store.db"
    con = sqlite3.connect(store_path)
    try:
        con.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data TEXT)")
        con.execute(
            "INSERT INTO blobs (id, data) VALUES (?, ?)",
            (
                "a" * 64,
                json.dumps(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": f"<user_query>{user_text}</user_query>"}],
                    }
                ),
            ),
        )
        con.execute(
            "INSERT INTO blobs (id, data) VALUES (?, ?)",
            (
                "b" * 64,
                json.dumps(
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hello from cursor cli"}],
                    }
                ),
            ),
        )
        con.commit()
    finally:
        con.close()
    return store_path


def _write_ide_transcript(root: Path, workspace: str, transcript_id: str, user_text: str) -> Path:
    slug = workspace.lstrip("/").replace("/", "-")
    transcript_dir = root / slug / "agent-transcripts" / transcript_id
    transcript_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = transcript_dir / f"{transcript_id}.jsonl"
    records = [
        {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": f"<user_query>{user_text}</user_query>",
                    }
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hello from cursor ide"}]
            },
        },
    ]
    jsonl_path.write_text("".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8")
    return jsonl_path


def test_load_cursor_cli_session(tmp_path: Path) -> None:
    store_path = _write_cli_store(tmp_path, "/repo", "11111111-1111-4111-8111-111111111111", "hi cli")
    imported = load_cursor_cli_session(store_path)
    assert imported.source == "cursor-cli"
    assert len(imported.items) == 2
    assert imported.items[0].data.model_dump()["content"][0]["text"] == "hi cli"


def test_read_cursor_cli_from_rowid(tmp_path: Path) -> None:
    store_path = _write_cli_store(tmp_path, "/repo", "11111111-1111-4111-8111-111111111111", "hi cli")
    first = read_cursor_cli_from_rowid(store_path, last_rowid=0)
    assert len(first.items) == 2
    second = read_cursor_cli_from_rowid(store_path, last_rowid=first.last_rowid)
    assert second.items == ()
    assert second.last_rowid == first.last_rowid


def test_iter_cursor_cli_chats_filters_recent(tmp_path: Path) -> None:
    store_path = _write_cli_store(tmp_path, "/repo", "11111111-1111-4111-8111-111111111111", "hi cli")
    chats = list(iter_cursor_cli_chats(tmp_path))
    assert len(chats) == 1
    assert chats[0].store_path == store_path


def test_load_cursor_ide_session(tmp_path: Path) -> None:
    transcript_path = _write_ide_transcript(
        tmp_path,
        "/Users/me/Project/runtime",
        "22222222-2222-4222-8222-222222222222",
        "hi ide",
    )
    imported = load_cursor_ide_session(transcript_path, workspace="/Users/me/Project/runtime")
    assert imported.source == "cursor-ide"
    assert len(imported.items) == 2
    assert imported.items[0].data.model_dump()["content"][0]["text"] == "hi ide"


def test_read_cursor_ide_from_offset(tmp_path: Path) -> None:
    transcript_path = _write_ide_transcript(
        tmp_path,
        "/Users/me/Project/runtime",
        "22222222-2222-4222-8222-222222222222",
        "hi ide",
    )
    first = read_cursor_ide_from_offset(transcript_path, byte_offset=0)
    assert len(first.items) == 2
    second = read_cursor_ide_from_offset(transcript_path, byte_offset=first.byte_offset)
    assert second.items == ()


@pytest.mark.asyncio
@respx.mock
async def test_cursor_cli_local_poller_imports_recent_chat(tmp_path: Path) -> None:
    chat_id = "33333333-3333-4333-8333-333333333333"
    store_path = _write_cli_store(tmp_path, "/repo", chat_id, "ambient cli")
    rowid = initial_cursor_cli_rowid(store_path)

    base_url = "http://testserver"
    host_id = _HOST_ID
    external_id = chat_id
    session_id = import_conversation_id("cursor-cli", external_id)

    respx.post(f"{base_url}/v1/imports").mock(
        return_value=httpx.Response(201, json={"session_id": session_id, "status": "imported", "item_count": 2})
    )

    async with httpx.AsyncClient(base_url=base_url, headers={HOST_AMBIENT_ID_HEADER: host_id}) as client:
        ctx = PollContext(server_url=base_url, host_id=host_id, client=client)
        poller = CursorCliLocalSubPoller(chats_root=tmp_path)
        state = await poller.poll_once(ctx, AmbientBridgeState(tracks={}))

    assert chat_id in state.tracks
    assert state.tracks[chat_id].session_id == session_id
    assert state.tracks[chat_id].byte_offset == rowid
    assert respx.calls.call_count == 1


def test_cursor_cli_poller_read_only_by_default() -> None:
    poller = CursorCliAmbientPoller()
    assert poller.read_only is True


def test_codex_poller_read_only_by_default() -> None:
    from omnigent.host.polling.pollers.codex import CodexAmbientPoller

    assert CodexAmbientPoller().read_only is True


def test_cursor_ide_poller_read_only_by_default() -> None:
    from omnigent.host.polling.pollers.cursor_ide import CursorIdeAmbientPoller

    assert CursorIdeAmbientPoller().read_only is True


def test_ambient_poller_read_only_can_be_overridden() -> None:
    poller = CursorCliAmbientPoller(read_only=False)
    assert poller.read_only is False


@pytest.mark.asyncio
async def test_cursor_cli_poller_disabled_by_default() -> None:
    poller = CursorCliAmbientPoller()
    async with httpx.AsyncClient() as client:
        ctx = PollContext(server_url="http://testserver", host_id=_HOST_ID, client=client)
        assert poller.enabled(ctx) is False
