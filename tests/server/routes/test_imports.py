"""Tests for importing normalized local harness sessions."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

from omnigent.ambient_codex import HOST_AMBIENT_ID_HEADER
from omnigent.db.utils import builtin_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


def _seed_claude_agent(db_uri: str) -> str:
    """Seed the built-in agent because focused app tests skip lifespan startup."""
    agent_id = builtin_agent_id("claude-native-ui")
    SqlAlchemyAgentStore(db_uri).create(
        agent_id,
        name="claude-native-ui",
        bundle_location="builtin://claude-native-ui",
    )
    return agent_id


def _seed_codex_agent(db_uri: str) -> str:
    agent_id = builtin_agent_id("codex-native-ui")
    SqlAlchemyAgentStore(db_uri).create(
        agent_id,
        name="codex-native-ui",
        bundle_location="builtin://codex-native-ui",
    )
    return agent_id


async def test_import_session_creates_normal_session_and_blocks_duplicate(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """An import creates one native session and a retry is rejected."""
    agent_id = _seed_claude_agent(db_uri)
    payload = {
        "source": "claude",
        "external_session_id": "claude-session-1",
        "workspace": "/repo",
        "items": [
            {
                "type": "message",
                "response_id": "claude:turn-1",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "inspect TODO.md"}],
                },
            },
            {
                "type": "message",
                "response_id": "claude:turn-1",
                "data": {
                    "role": "assistant",
                    "agent": "claude-native-ui",
                    "content": [{"type": "output_text", "text": "Done."}],
                },
            },
        ],
    }

    created = await client.post("/v1/imports", json=payload)
    repeated = await client.post("/v1/imports", json=payload)

    assert created.status_code == 201
    assert created.json()["status"] == "imported"
    assert repeated.status_code == 409
    assert created.json()["session_id"] in repeated.text
    assert "already been imported" in repeated.text

    session_id = created.json()["session_id"]
    conversation = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conversation is not None
    assert conversation.agent_id == agent_id
    assert conversation.external_session_id == "claude-session-1"
    assert conversation.workspace == "/repo"
    assert conversation.title == "inspect TODO.md"
    assert conversation.labels["omnigent.wrapper"] == "claude-code-native-ui"
    items = await client.get(f"/v1/sessions/{session_id}/items")
    assert items.status_code == 200
    assert [item["type"] for item in items.json()["data"]] == ["message", "message"]


async def test_concurrent_identical_imports_return_one_session(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Concurrent retries serialize on source identity and one is rejected."""
    _seed_claude_agent(db_uri)
    payload = {
        "source": "claude",
        "external_session_id": "claude-concurrent-1",
        "items": [
            {
                "type": "message",
                "response_id": "claude:turn-1",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                },
            }
        ],
    }

    first, second = await asyncio.gather(
        client.post("/v1/imports", json=payload),
        client.post("/v1/imports", json=payload),
    )

    assert {first.status_code, second.status_code} == {201, 409}
    imported = SqlAlchemyConversationStore(db_uri).find_imported_conversation(
        "claude", "claude-concurrent-1"
    )
    assert imported is not None


async def test_import_session_rejects_empty_history(client: httpx.AsyncClient) -> None:
    """An empty parser result cannot create a permanently claimed session."""
    response = await client.post(
        "/v1/imports",
        json={
            "source": "codex",
            "external_session_id": "empty-codex-session",
            "items": [],
        },
    )

    assert response.status_code == 422


async def test_import_codex_with_ambient_cursor_persists_metadata(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Codex imports can claim ambient polling state for one host."""
    _seed_codex_agent(db_uri)
    host_id = "b" * 32
    payload = {
        "source": "codex",
        "external_session_id": "019e96aa-0be2-7343-8d3b-6f914d60936b",
        "workspace": "/repo",
        "items": [
            {
                "type": "message",
                "response_id": "codex:turn-1",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello codex"}],
                },
            }
        ],
        "ambient_codex": {
            "byte_offset": 128,
            "turn_id": "history",
            "rollout_path": "/home/user/.codex/sessions/rollout.jsonl",
            "connection_id": None,
        },
    }

    created = await client.post(
        "/v1/imports",
        json=payload,
        headers={HOST_AMBIENT_ID_HEADER: host_id},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    store = SqlAlchemyConversationStore(db_uri)
    tracks = store.list_ambient_codex_tracks(host_id)
    assert len(tracks) == 1
    assert tracks[0].session_id == session_id
    assert tracks[0].byte_offset == 128
    assert tracks[0].rollout_path.endswith("rollout.jsonl")


async def test_import_session_announces_session_added(
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Imported sessions push a session_added discovery event for the sidebar."""
    _seed_claude_agent(db_uri)
    announce = MagicMock()
    monkeypatch.setattr("omnigent.server.routes.imports._announce_session_added", announce)
    payload = {
        "source": "claude",
        "external_session_id": "claude-ambient-discover-1",
        "items": [
            {
                "type": "message",
                "response_id": "claude:turn-1",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                },
            }
        ],
    }

    created = await client.post("/v1/imports", json=payload)

    assert created.status_code == 201
    announce.assert_called_once_with(None, created.json()["session_id"])
