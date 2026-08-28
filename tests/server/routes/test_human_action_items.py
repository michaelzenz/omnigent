"""Route tests for human action task items (``kind="human_action"``)."""

from __future__ import annotations

import uuid

import httpx

from omnigent.agent_tasks.event_types import HUMAN_ACTION_DONE_EVENT_TYPE
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


async def _create_pending_task(client: httpx.AsyncClient, seed: str) -> str:
    # Pending tasks skip the inline manager bootstrap.
    resp = await client.post(
        "/v1/agent-tasks",
        json={"title": f"task-{seed}", "goal": "goal", "state": "pending"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _create_human_action(client: httpx.AsyncClient, task_id: str, title: str) -> dict:
    resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            "title": title,
            "description": "Only you can do this",
            "kind": "human_action",
            "state": "draft",
            "submit_for_user_ack": True,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_create_human_action_item(client: httpx.AsyncClient) -> None:
    task_id = await _create_pending_task(client, "ha-route-create")
    body = await _create_human_action(client, task_id, "Rotate the key")
    assert body["kind"] == "human_action"
    assert body["state"] == "pending"
    assert body["worker_id"] is None
    assert body["instructions"] is None


async def test_create_human_action_rejects_instructions(client: httpx.AsyncClient) -> None:
    task_id = await _create_pending_task(client, "ha-route-instructions")
    resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={"title": "Bad", "kind": "human_action", "instructions": "steps"},
    )
    assert resp.status_code == 400


async def test_create_item_rejects_unknown_kind(client: httpx.AsyncClient) -> None:
    task_id = await _create_pending_task(client, "ha-route-bad-kind")
    resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={"title": "Bad", "kind": "robot_action"},
    )
    assert resp.status_code == 422


async def test_resolve_mark_done(client: httpx.AsyncClient, db_uri: str) -> None:
    task_id = await _create_pending_task(client, "ha-route-mark-done")
    item = await _create_human_action(client, task_id, "Rotate the key")

    resp = await client.post(
        f"/v1/task-items/{item['id']}/resolve",
        json={"resolution": "mark_done"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "done"
    assert resp.json()["kind"] == "human_action"

    event_store = SqlAlchemyTaskEventStore(db_uri)
    events = event_store.list_events(state="routed", task_id=task_id)
    assert [event.event_type for event in events] == [HUMAN_ACTION_DONE_EVENT_TYPE]

    # Done is terminal: a second mark_done conflicts.
    resp = await client.post(
        f"/v1/task-items/{item['id']}/resolve",
        json={"resolution": "mark_done"},
    )
    assert resp.status_code == 409


async def test_mark_done_rejects_work_item(client: httpx.AsyncClient) -> None:
    task_id = await _create_pending_task(client, "ha-route-work-item")
    resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={"title": "Regular work", "state": "pending"},
    )
    assert resp.status_code == 200, resp.text
    item_id = resp.json()["id"]
    resp = await client.post(
        f"/v1/task-items/{item_id}/resolve",
        json={"resolution": "mark_done"},
    )
    assert resp.status_code == 409


async def test_dismiss_human_action_emits_no_event(client: httpx.AsyncClient, db_uri: str) -> None:
    task_id = await _create_pending_task(client, "ha-route-dismiss")
    item = await _create_human_action(client, task_id, "Rotate the key")

    resp = await client.post(
        f"/v1/task-items/{item['id']}/resolve",
        json={"resolution": "reject_item"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "cancelled"

    event_store = SqlAlchemyTaskEventStore(db_uri)
    assert event_store.list_events(state="routed", task_id=task_id) == []


async def test_accept_human_action_rejected_with_clear_message(client: httpx.AsyncClient) -> None:
    task_id = await _create_pending_task(client, "ha-route-accept")
    item = await _create_human_action(client, task_id, "Rotate the key")

    resp = await client.post(
        f"/v1/task-items/{item['id']}/resolve",
        json={"resolution": "accept_item"},
    )
    assert resp.status_code == 409
    assert "marked done or dismissed" in resp.text
