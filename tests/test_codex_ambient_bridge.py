"""Tests for ambient Codex rollout mirroring into Omnigent."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from omnigent.host.codex_ambient_bridge import (
    _BridgeState,
    _TrackedRollout,
    _poll_codex_ambient_once,
    codex_ambient_sync_enabled,
)
from omnigent.session_import.codex_rollout import (
    read_codex_rollout_from_offset,
    thread_id_from_rollout_path,
)
from omnigent.session_import.models import import_conversation_id


def _write_rollout(path: Path, session_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": "/repo"},
        },
        {
            "type": "turn_context",
            "payload": {"turn_id": "turn_1", "cwd": "/repo"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello from codex"}],
            },
        },
    ]
    path.write_text("".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8")


def test_thread_id_from_rollout_path() -> None:
    session_id = "019e96aa-0be2-7343-8d3b-6f914d60936b"
    path = Path(f"rollout-2026-07-15T12-00-00-{session_id}.jsonl")
    assert thread_id_from_rollout_path(path) == session_id


def test_read_codex_rollout_from_offset_appends_items(tmp_path: Path) -> None:
    session_id = "019e96aa-0be2-7343-8d3b-6f914d60936b"
    rollout = (
        tmp_path
        / "sessions"
        / "2026"
        / "07"
        / "15"
        / f"rollout-2026-07-15T12-00-00-{session_id}.jsonl"
    )
    _write_rollout(rollout, session_id)
    first = read_codex_rollout_from_offset(rollout, byte_offset=0)
    assert len(first.items) == 1
    assert first.items[0].data.model_dump()["content"][0]["text"] == "hello from codex"

    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "hi there"}],
                    },
                }
            )
            + "\n"
        )
    second = read_codex_rollout_from_offset(
        rollout,
        byte_offset=first.byte_offset,
        turn_id=first.turn_id,
        workspace=first.workspace,
    )
    assert len(second.items) == 1
    assert second.items[0].data.model_dump()["content"][0]["text"] == "hi there"


def test_import_conversation_id_is_stable() -> None:
    session_id = "019e96aa-0be2-7343-8d3b-6f914d60936b"
    assert import_conversation_id("codex", session_id) == import_conversation_id(
        "codex", session_id
    )


@pytest.mark.asyncio
@respx.mock
async def test_poll_codex_ambient_once_imports_recent_rollout(tmp_path: Path) -> None:
    session_id = "019e96aa-0be2-7343-8d3b-6f914d60936b"
    rollout = (
        tmp_path
        / "sessions"
        / "2026"
        / "07"
        / "15"
        / f"rollout-2026-07-15T12-00-00-{session_id}.jsonl"
    )
    _write_rollout(rollout, session_id)
    state_path = tmp_path / "bridge.json"
    state = _BridgeState(threads={}, started_at_ms=1)

    import_route = respx.post("http://test/v1/imports").mock(
        return_value=httpx.Response(
            201,
            json={
                "session_id": import_conversation_id("codex", session_id),
                "status": "imported",
                "item_count": 1,
            },
        )
    )

    async with httpx.AsyncClient(base_url="http://test") as client:
        updated = await _poll_codex_ambient_once(client, state=state, codex_home=tmp_path)

    assert import_route.called
    assert session_id in updated.threads
    tracked = updated.threads[session_id]
    assert tracked.session_id == import_conversation_id("codex", session_id)
    assert tracked.byte_offset == rollout.stat().st_size

    updated.save(state_path)
    reloaded = _BridgeState.load(state_path)
    assert reloaded.threads[session_id].session_id == tracked.session_id


@pytest.mark.asyncio
@respx.mock
async def test_poll_codex_ambient_once_tails_new_items(tmp_path: Path) -> None:
    session_id = "019e96aa-0be2-7343-8d3b-6f914d60936b"
    rollout = (
        tmp_path
        / "sessions"
        / "2026"
        / "07"
        / "15"
        / f"rollout-2026-07-15T12-00-00-{session_id}.jsonl"
    )
    _write_rollout(rollout, session_id)
    omnigent_session_id = import_conversation_id("codex", session_id)
    state = _BridgeState(
        threads={
            session_id: _TrackedRollout(
                thread_id=session_id,
                rollout_path=str(rollout),
                session_id=omnigent_session_id,
                byte_offset=rollout.stat().st_size,
                turn_id="turn_1",
                workspace="/repo",
            )
        },
        started_at_ms=1,
    )

    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "follow-up"}],
                    },
                }
            )
            + "\n"
        )

    event_route = respx.post(f"http://test/v1/sessions/{omnigent_session_id}/events").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    async with httpx.AsyncClient(base_url="http://test") as client:
        updated = await _poll_codex_ambient_once(
            client,
            state=state,
            codex_home=tmp_path,
            state_path=tmp_path / "bridge.json",
        )

    assert event_route.called
    assert updated.threads[session_id].byte_offset == rollout.stat().st_size


@pytest.mark.asyncio
@respx.mock
async def test_poll_codex_ambient_once_deletes_removed_codex_session(tmp_path: Path) -> None:
    session_id = "019e96aa-0be2-7343-8d3b-6f914d60936b"
    omnigent_session_id = import_conversation_id("codex", session_id)
    state = _BridgeState(
        threads={
            session_id: _TrackedRollout(
                thread_id=session_id,
                rollout_path=str(
                    tmp_path
                    / "sessions"
                    / "2026"
                    / "07"
                    / "15"
                    / f"rollout-2026-07-15T12-00-00-{session_id}.jsonl"
                ),
                session_id=omnigent_session_id,
                byte_offset=0,
                turn_id="turn_1",
                workspace="/repo",
            )
        },
        started_at_ms=1,
    )

    delete_route = respx.delete(f"http://test/v1/sessions/{omnigent_session_id}").mock(
        return_value=httpx.Response(200, json={"id": omnigent_session_id, "deleted": True})
    )

    async with httpx.AsyncClient(base_url="http://test") as client:
        updated = await _poll_codex_ambient_once(
            client,
            state=state,
            codex_home=tmp_path,
            state_path=tmp_path / "bridge.json",
        )

    assert delete_route.called
    assert session_id not in updated.threads


def test_codex_ambient_sync_enabled_respects_env_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("host:\n  codex_ambient_sync: false\n", encoding="utf-8")
    assert codex_ambient_sync_enabled(config_path=config_path) is False

    config_path.write_text("host:\n  codex_ambient_sync: true\n", encoding="utf-8")
    assert codex_ambient_sync_enabled(config_path=config_path) is True

    monkeypatch.setenv("OMNIGENT_CODEX_AMBIENT_SYNC", "0")
    assert codex_ambient_sync_enabled(config_path=config_path) is False
