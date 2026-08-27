"""Tests for the Onih Pi canonical-rebuild session store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from omnigent.onih_pi_session_store import OnihPiSessionStore

_PI_SESSION_ID = "019efdb8-54c8-7c02-be27-875eb2620635"


def _user_item(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }


def _assistant_item(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _error_item() -> dict[str, Any]:
    return {
        "type": "error",
        "source": "execution",
        "code": "RuntimeError",
        "message": "inner executor error: Failed to start Pi",
    }


def _user_file_item(text: str, mime: str = "text/plain") -> dict[str, Any]:
    import base64

    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": "fix this file:"},
            {"type": "input_file", "file_data": f"data:{mime};base64,{encoded}"},
        ],
    }


def _user_image_item() -> dict[str, Any]:
    return {
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": "describe this:"},
            {"type": "input_image", "image_url": "data:image/png;base64,iVBOR="},
        ],
    }


def test_validate_items_accepts_error_items() -> None:
    items = [_user_item("q"), _error_item(), _assistant_item("a")]
    normalized = OnihPiSessionStore._validate_items(items)
    assert [item["type"] for item in normalized] == ["message", "error", "message"]


def test_validate_items_still_rejects_unknown_types() -> None:
    with pytest.raises(ValueError, match="unsupported canonical item"):
        OnihPiSessionStore._validate_items([{"type": "reasoning"}])


def test_rebuild_with_error_item_writes_session_without_it(tmp_path: Path) -> None:
    store = OnihPiSessionStore(tmp_path)
    try:
        staging = store.rebuild(
            conversation_id="conv_abc",
            pi_session_id=_PI_SESSION_ID,
            items=[_user_item("q"), _error_item(), _assistant_item("a")],
            workspace=Path("/repo"),
            provider="omnigent",
            model="claude-opus-4-8",
        )
        session_files = list(staging.glob("*.jsonl"))
        assert len(session_files) == 1
        records = [
            json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()
        ]
        assert records[0]["type"] == "session"
        assert [r["message"]["role"] for r in records[1:]] == ["user", "assistant"]
        assert "inner executor error" not in json.dumps(records)
    finally:
        store.close()


def test_validate_items_converts_text_file_to_input_text() -> None:
    """A pasted text file (input_file) is decoded into input_text for Pi."""
    items = [_user_file_item("hello world")]
    normalized = OnihPiSessionStore._validate_items(items)
    content = normalized[0]["content"]
    assert all(block["type"] == "input_text" for block in content)
    assert content[1]["text"] == "hello world"


def test_validate_items_skips_binary_file() -> None:
    """Non-text input_file blocks are skipped, not fatal."""
    items = [_user_file_item("binary", mime="application/octet-stream")]
    normalized = OnihPiSessionStore._validate_items(items)
    content = normalized[0]["content"]
    # Only the input_text block survives; the binary file is dropped.
    assert len(content) == 1
    assert content[0]["type"] == "input_text"


def test_validate_items_skips_input_image() -> None:
    """input_image blocks are skipped, not fatal."""
    items = [_user_image_item()]
    normalized = OnihPiSessionStore._validate_items(items)
    content = normalized[0]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "input_text"
    assert content[0]["text"] == "describe this:"


def _function_call_item(call_id: str, name: str = "read") -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": "{}",
    }


def _function_output_item(call_id: str, output: str = "ok") -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "name": "read",
        "output": output,
        "tool_status": "success",
    }


def _interrupted_assistant_item(text: str, response_id: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "interrupted": True,
        "response_id": response_id,
        "content": [{"type": "output_text", "text": text}],
    }


def test_validate_items_drops_unpaired_tool_result() -> None:
    """An unpaired function_call_output is dropped, not fatal."""
    items = [
        _user_item("q"),
        _function_output_item("call_orphan"),
        _assistant_item("a"),
    ]
    normalized = OnihPiSessionStore._validate_items(items)
    types = [item["type"] for item in normalized]
    assert "function_call_output" not in types
    assert types == ["message", "message"]


def test_validate_items_skips_interrupted_turn_with_unpaired_output() -> None:
    """Items from an interrupted turn (including unpaired outputs) are skipped."""
    items = [
        _user_item("q"),
        _assistant_item("prior answer"),
        {**_user_item("q2"), "response_id": "resp_interrupted"},
        {**_function_call_item("call_1"), "response_id": "resp_interrupted"},
        {**_function_output_item("call_1"), "response_id": "resp_interrupted"},
        # Unpaired output from the interrupted turn (e.g. native Pi tool race).
        {**_function_output_item("call_orphan"), "response_id": "resp_interrupted"},
        _interrupted_assistant_item("partial...", "resp_interrupted"),
    ]
    normalized = OnihPiSessionStore._validate_items(items)
    # Everything from resp_interrupted is skipped; only the prior turn survives.
    rids = [item.get("response_id") for item in normalized]
    assert "resp_interrupted" not in rids
    assert len(normalized) == 2


def test_rebuild_with_unpaired_output_does_not_raise(tmp_path: Path) -> None:
    """A session with an unpaired tool result rebuilds without error."""
    store = OnihPiSessionStore(tmp_path)
    try:
        staging = store.rebuild(
            conversation_id="conv_abc",
            pi_session_id=_PI_SESSION_ID,
            items=[
                _user_item("q"),
                _function_output_item("call_orphan"),
                _assistant_item("a"),
            ],
            workspace=Path("/repo"),
            provider="omnigent",
            model="claude-opus-4-8",
        )
        session_files = list(staging.glob("*.jsonl"))
        assert len(session_files) == 1
        records = [
            json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()
        ]
        # Header + user + assistant; the orphan tool result is dropped.
        assert len(records) == 3
        assert records[0]["type"] == "session"
        assert [r["message"]["role"] for r in records[1:]] == ["user", "assistant"]
    finally:
        store.close()
