"""Route tests for managed task items (``/v1/agent-tasks/{task_id}/items``)."""

from __future__ import annotations

import uuid

import httpx

from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


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

    task_id = _uid("items-list-task")
    SqlAlchemyTaskStore(db_uri).create(task_id, "CI failure", "CI passes", state="active")
    # Promote the event to routed on the task so the items endpoint can claim it.
    event_store.update_event(event_id, task_id=task_id, state="routed")

    created = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            "title": "Investigate CI",
            "event_ids": [event_id],
            "instructions": "Read workflow logs",
            "internal_note": "workflow run 42 failed on lint",
            "submit_for_user_ack": True,
        },
    )
    assert created.status_code == 200, created.text

    resp = await client.get(
        f"/v1/agent-tasks/{task_id}/items",
        params={"state": "pending"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert len(items) == 1
    assert items[0]["state"] == "pending"
    assert items[0]["internal_note"] == "workflow run 42 failed on lint"
