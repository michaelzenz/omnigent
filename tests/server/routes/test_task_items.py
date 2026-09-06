"""Route tests for managed task items (``/v1/agent-tasks/{task_id}/items``)."""

from __future__ import annotations

import uuid

import httpx

from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


async def test_list_task_items_filters_by_state(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """GET items supports state filters and returns internal_note."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("items-list-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
    )

    created = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "CI failure",
            "goal": "CI passes",
            "items": [
                {
                    "title": "Investigate CI",
                    "event_ids": [event_id],
                    "instructions": "Read workflow logs",
                    "internal_note": "workflow run 42 failed on lint",
                },
            ],
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    resp = await client.get(
        f"/v1/agent-tasks/{task_id}/items",
        params={"state": "pending"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert len(items) == 1
    assert items[0]["state"] == "pending"
    assert items[0]["internal_note"] == "workflow run 42 failed on lint"
