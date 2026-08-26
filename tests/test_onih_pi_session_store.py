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
