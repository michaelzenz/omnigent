"""Tests for Cursor projects ambient session import and polling."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from omnigent.ambient_codex import HOST_AMBIENT_ID_HEADER
from omnigent.host.polling.pollers.cursor_projects import CursorProjectsAmbientPoller
from omnigent.host.polling.pollers.cursor_projects_local import CursorProjectsLocalSubPoller
from omnigent.host.polling.pollers.ambient_state import AmbientBridgeState
from omnigent.host.polling.context import PollContext
from omnigent.session_import.cursor_projects import (
    initial_cursor_projects_byte_offset,
    iter_cursor_projects_transcripts,
    load_cursor_projects_session,
    read_cursor_projects_from_offset,
)
from omnigent.session_import.models import import_conversation_id

_HOST_ID = "b" * 32


def _write_projects_transcript(root: Path, workspace: str, transcript_id: str, user_text: str) -> Path:
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
                "content": [{"type": "text", "text": "hello from cursor projects"}]
            },
        },
    ]
    jsonl_path.write_text("".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8")
    return jsonl_path


def test_load_cursor_projects_session(tmp_path: Path) -> None:
    transcript_path = _write_projects_transcript(
        tmp_path,
        "/Users/me/Project/runtime",
        "22222222-2222-4222-8222-222222222222",
        "hi projects",
    )
    imported = load_cursor_projects_session(transcript_path, workspace="/Users/me/Project/runtime")
    assert imported.source == "cursor-projects"
    assert len(imported.items) == 2
    assert imported.items[0].data.model_dump()["content"][0]["text"] == "hi projects"


def test_read_cursor_projects_from_offset(tmp_path: Path) -> None:
    transcript_path = _write_projects_transcript(
        tmp_path,
        "/Users/me/Project/runtime",
        "22222222-2222-4222-8222-222222222222",
        "hi projects",
    )
    first = read_cursor_projects_from_offset(transcript_path, byte_offset=0)
    assert len(first.items) == 2
    second = read_cursor_projects_from_offset(transcript_path, byte_offset=first.byte_offset)
    assert second.items == ()


def test_iter_cursor_projects_transcripts_filters_recent(tmp_path: Path) -> None:
    transcript_path = _write_projects_transcript(
        tmp_path,
        "/repo",
        "11111111-1111-4111-8111-111111111111",
        "hi projects",
    )
    transcripts = list(iter_cursor_projects_transcripts(tmp_path))
    assert len(transcripts) == 1
    assert transcripts[0].transcript_path == transcript_path


@pytest.mark.asyncio
@respx.mock
async def test_cursor_projects_local_poller_imports_recent_transcript(tmp_path: Path) -> None:
    transcript_id = "33333333-3333-4333-8333-333333333333"
    transcript_path = _write_projects_transcript(tmp_path, "/repo", transcript_id, "ambient projects")
    byte_offset = initial_cursor_projects_byte_offset(transcript_path)

    base_url = "http://testserver"
    host_id = _HOST_ID
    external_id = transcript_id
    session_id = import_conversation_id("cursor-projects", external_id)

    respx.post(f"{base_url}/v1/imports").mock(
        return_value=httpx.Response(201, json={"session_id": session_id, "status": "imported", "item_count": 2})
    )

    async with httpx.AsyncClient(base_url=base_url, headers={HOST_AMBIENT_ID_HEADER: host_id}) as client:
        ctx = PollContext(server_url=base_url, host_id=host_id, client=client)
        poller = CursorProjectsLocalSubPoller(projects_root=tmp_path)
        state = await poller.poll_once(ctx, AmbientBridgeState(tracks={}))

    assert transcript_id in state.tracks
    assert state.tracks[transcript_id].session_id == session_id
    assert state.tracks[transcript_id].byte_offset == byte_offset
    assert respx.calls.call_count == 1


def test_cursor_projects_poller_read_only_by_default() -> None:
    poller = CursorProjectsAmbientPoller()
    assert poller.read_only is True


def test_codex_poller_read_only_by_default() -> None:
    from omnigent.host.polling.pollers.codex import CodexAmbientPoller

    assert CodexAmbientPoller().read_only is True


def test_ambient_poller_read_only_can_be_overridden() -> None:
    poller = CursorProjectsAmbientPoller(read_only=False)
    assert poller.read_only is False


@pytest.mark.asyncio
async def test_cursor_projects_poller_disabled_by_default() -> None:
    poller = CursorProjectsAmbientPoller()
    async with httpx.AsyncClient() as client:
        ctx = PollContext(server_url="http://testserver", host_id=_HOST_ID, client=client)
        assert poller.enabled(ctx) is False
