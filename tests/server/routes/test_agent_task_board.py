"""Route tests for the agent task board (``GET /v1/agent-tasks/board/pending``)."""

from __future__ import annotations

import uuid

import httpx

from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


async def test_board_pending_lists_open_fyi_clusters(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """GET board/pending returns open FYI clusters with linked events."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("board-fyi-event")
    event_store.create_event(
        event_id,
        "build.finished",
        "Nightly build passed",
        state="awaiting_grouping",
    )

    created = await client.post(
        "/v1/task-events/fyi-clusters",
        json={
            "event_ids": [event_id],
            "headline": "Nightly build green",
        },
    )
    cluster_id = created.json()["id"]

    resp = await client.get("/v1/agent-tasks/board/pending")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "agent.task.board"
    card = next(card for card in body["fyi"] if card["id"] == cluster_id)
    assert card["headline"] == "Nightly build green"
    assert len(card["body"]["events"]) == 1
    assert card["body"]["events"][0]["id"] == event_id
