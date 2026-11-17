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
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_asset_store.sqlalchemy_store import SqlAlchemyTaskAssetStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}


def _task(
    db_uri: str,
    title: str,
    *,
    state: str = "active",
    owner: str = ALICE_ID,
    manager_id: str | None = None,
) -> str:
    task_id = uuid.uuid4().hex
    SqlAlchemyTaskStore(db_uri).create(
        task_id,
        owner_user_id=owner,
        title=title,
        state=state,
        manager_id=manager_id,
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
    client: httpx.AsyncClient, headers: dict[str, str] = ALICE, **params: int | str
) -> dict[str, Any]:
    response = await client.get("/v1/agent-tasks/board", params=params, headers=headers)
    assert response.status_code == 200
    return response.json()


async def test_the_board_reads_the_live_tasks_in_board_order_a_page_at_a_time_down_and_up(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    # Each new task goes to the top of its owner's board.
    resolved = _task(db_uri, "Resolved", state="agent-resolved")
    _task(db_uri, "Archived", state="archived")
    idle = _task(db_uri, "Idle", state="idle")
    active = _task(db_uri, "Active", manager_id=uuid.uuid4().hex)
    _task(db_uri, "Bob's", owner=BOB_ID)

    first = await _board(auth_client, limit=2)
    rest = await _board(auth_client, limit=2, after=first["next_cursor"])
    back = await _board(auth_client, limit=2, before=rest["prev_cursor"])
    whole = await _board(auth_client)
    bobs = await _board(auth_client, headers=BOB)

    assert ([card["id"] for card in first["data"]], first["has_more"], first["prev_cursor"]) == (
        [active, idle],
        True,
        None,
    )
    assert ([card["id"] for card in rest["data"]], rest["has_more"], rest["next_cursor"]) == (
        [resolved],
        False,
        None,
    )
    assert ([card["id"] for card in back["data"]], back["next_cursor"], back["prev_cursor"]) == (
        [active, idle],
        first["next_cursor"],
        None,
    )
    assert ([card["id"] for card in whole["data"]], whole["has_more"]) == (
        [active, idle, resolved],
        False,
    )
    assert "internal_note" not in whole["data"][0]
    assert [card["title"] for card in bobs["data"]] == ["Bob's"]
    # The count covers the whole board, not only the page.
    assert [board["unmanaged_count"] for board in (first, rest, whole, bobs)] == [2, 2, 2, 1]


async def test_a_board_read_refuses_a_cursor_that_the_board_did_not_send(
    auth_client: httpx.AsyncClient,
) -> None:
    reads = [
        await auth_client.get("/v1/agent-tasks/board", params=params, headers=ALICE)
        for params in (
            {"after": "not-a-cursor"},
            {"before": "5:not-a-task-id"},
            {"after": f"{10**40}:{uuid.uuid4().hex}"},
            {"after": f"5:{uuid.uuid4().hex}", "before": f"7:{uuid.uuid4().hex}"},
            {"q": "fix", "after": f"5:{uuid.uuid4().hex}"},
            {"q": "fix", "before": "²"},
        )
    ]

    assert [read.status_code for read in reads] == [400, 400, 400, 400, 400, 400]


async def test_a_board_read_past_either_end_returns_the_page_at_that_end(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    low = _task(db_uri, "Low")
    middle = _task(db_uri, "Middle")
    high = _task(db_uri, "High")
    gone = uuid.uuid4().hex

    above_top = await _board(auth_client, limit=2, before=f"{2**62}:{gone}")
    below_bottom = await _board(auth_client, limit=2, after=f"{-(2**62)}:{gone}")
    past_matches = await _board(auth_client, q="low", limit=1, after="5")

    assert ([card["id"] for card in above_top["data"]], above_top["prev_cursor"]) == (
        [high, middle],
        None,
    )
    assert ([card["id"] for card in below_bottom["data"]], below_bottom["has_more"]) == (
        [middle, low],
        False,
    )
    assert below_bottom["prev_cursor"] is not None
    assert [card["id"] for card in past_matches["data"]] == [low]


async def test_a_search_returns_the_cards_that_hold_the_most_words_first_a_page_at_a_time(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    platform = SqlAlchemyManagerStore(db_uri).create(
        uuid.uuid4().hex, owner_user_id=ALICE_ID, title="Platform", conversation_id=None
    )
    one_word = _task(db_uri, "Fix the logs", manager_id=platform.id)
    both_words = _task(db_uri, "Fix the retries")
    _task(db_uri, "Docs")
    _task(db_uri, "Archived retries fix", state="archived")
    _task(db_uri, "Fix Bob's retries", owner=BOB_ID)

    found = await _board(auth_client, q="retries FIX")
    first = await _board(auth_client, q="retries FIX", limit=1)
    rest = await _board(auth_client, q="retries FIX", limit=1, after=first["next_cursor"])
    back = await _board(auth_client, q="retries FIX", limit=1, before=rest["prev_cursor"])
    by_manager_name = await _board(auth_client, q="platform")
    plain = await _board(auth_client)

    assert [(card["id"], card["match"]["hits"]) for card in found["data"]] == [
        (both_words, 2),
        (one_word, 1),
    ]
    assert ([card["id"] for card in first["data"]], first["has_more"]) == ([both_words], True)
    assert ([card["id"] for card in rest["data"]], rest["has_more"]) == ([one_word], False)
    assert ([card["id"] for card in back["data"]], back["prev_cursor"]) == ([both_words], None)
    # The manager's name is not searched.
    assert by_manager_name["data"] == []
    assert {card["match"] is None for card in plain["data"]} == {True}


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
    # Alice saw the newer session at 1_500 and then marked it unread.
    monkeypatch.setitem(sessions._read_last_seen, ALICE_ID, {newer: 1_500})
    monkeypatch.setitem(sessions._read_explicit_unread, ALICE_ID, {newer})
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
            worker["session"]["viewer_last_seen"],
            worker["session"]["viewer_unread"],
        )
        for worker in card["workers"]
    ] == [
        (busy, None, "Ran tests", "running", None, False),
        (newer, "Fixing retries", "Wrote the fix", "idle", 1_500, True),
        (older, None, "Read the logs", "idle", None, False),
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
