"""Tests for the GlobalHub board cards (``GET /v1/agent-tasks/board``)."""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import httpx
import pytest

from omnigent.server.routes import sessions
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store import sqlalchemy_store as conversation_tables
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_asset_store.sqlalchemy_store import SqlAlchemyTaskAssetStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}


def _task(db_uri: str, title: str, *, state: str = "active", owner: str = ALICE_ID) -> str:
    task_id = uuid.uuid4().hex
    SqlAlchemyTaskStore(db_uri).create(
        task_id, owner_user_id=owner, title=title, goal=f"Finish {title}", state=state
    )
    return task_id


def _session(db_uri: str, title: str) -> str:
    agent_id = uuid.uuid4().hex
    SqlAlchemyAgentStore(db_uri).create(agent_id, f"agent-{agent_id}", "bundles/agent.tar.gz")
    conversations = SqlAlchemyConversationStore(db_uri)
    return conversations.create_conversation(agent_id=agent_id, title=title).id


def _worker(db_uri: str, task_id: str, session_id: str, title: str | None = None) -> None:
    SqlAlchemyWorkerStore(db_uri).create(
        uuid.uuid4().hex,
        owner_user_id=ALICE_ID,
        task_id=task_id,
        session_id=session_id,
        title=title,
    )


async def _board(
    client: httpx.AsyncClient, headers: dict[str, str] = ALICE, **params: int
) -> dict[str, Any]:
    response = await client.get("/v1/agent-tasks/board", params=params, headers=headers)
    assert response.status_code == 200
    return response.json()


async def test_the_board_reads_the_live_tasks_in_board_order_a_window_at_a_time(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    # Each new task goes to the top of its owner's board.
    resolved = _task(db_uri, "Resolved", state="agent-resolved")
    _task(db_uri, "Archived", state="archived")
    idle = _task(db_uri, "Idle", state="idle")
    active = _task(db_uri, "Active")
    _task(db_uri, "Bob's", owner=BOB_ID)

    window = await _board(auth_client, limit=2)
    whole = await _board(auth_client)
    bobs = await _board(auth_client, headers=BOB)

    assert ([card["id"] for card in window["data"]], window["has_more"]) == (
        [active, idle],
        True,
    )
    assert ([card["id"] for card in whole["data"]], whole["has_more"]) == (
        [active, idle, resolved],
        False,
    )
    assert "internal_note" not in whole["data"][0]
    assert [card["title"] for card in bobs["data"]] == ["Bob's"]


async def test_a_card_lists_its_workers_busy_first_then_most_recent_and_its_assets(
    auth_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = _task(db_uri, "S3 retries")
    with monkeypatch.context() as clock:
        # Each session is newer than the one before it.
        clock.setattr(conversation_tables, "now_epoch", itertools.count(1_000).__next__)
        older, newer, busy = [
            _session(db_uri, title) for title in ("Read the logs", "Wrote the fix", "Ran tests")
        ]
    deleted_session = uuid.uuid4().hex
    for session_id, title in [
        (older, None),
        (newer, "Fixing retries"),
        (busy, None),
        (deleted_session, None),
    ]:
        _worker(db_uri, task_id, session_id, title)
    monkeypatch.setitem(sessions._session_status_cache, busy, "running")
    SqlAlchemyTaskAssetStore(db_uri).upsert(
        uuid.uuid4().hex,
        owner_user_id=ALICE_ID,
        task_id=task_id,
        url="https://example.com/design-doc",
        title="Design doc",
        category="documents",
        add_related_session_ids=[newer],
    )

    card = (await _board(auth_client))["data"][0]

    assert [
        (
            worker["session_id"],
            worker["title"],
            worker["session"]["title"],
            worker["session"]["status"],
        )
        for worker in card["workers"]
    ] == [
        (busy, None, "Ran tests", "running"),
        (newer, "Fixing retries", "Wrote the fix", "idle"),
        (older, None, "Read the logs", "idle"),
    ]
    assert [
        (asset["title"], asset["category"], asset["related_session_ids"])
        for asset in card["assets"]
    ] == [("Design doc", "documents", [newer])]


async def test_a_worker_whose_turn_lost_its_runner_reads_idle(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id = _task(db_uri, "S3 retries")
    session_id = _session(db_uri, "Ran tests")
    conversations = SqlAlchemyConversationStore(db_uri)
    # The row reads mid-turn, but its runner has no tunnel and no fresh heartbeat.
    assert conversations.set_runner_id(session_id, f"runner_{session_id}")
    conversations.set_session_live_status(session_id, "running")
    _worker(db_uri, task_id, session_id)

    card = (await _board(auth_client))["data"][0]

    stored = conversations.get_conversation(session_id)
    assert stored is not None
    assert (card["workers"][0]["session"]["status"], stored.live_status) == ("idle", "idle")
