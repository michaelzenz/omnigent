"""Tests for ``/v1/timer-items`` routes."""

from __future__ import annotations

import uuid

import httpx

from omnigent.ambient_codex import HOST_AMBIENT_ID_HEADER
from omnigent.db.utils import now_epoch
from omnigent.stores.timer_item_store.sqlalchemy_store import SqlAlchemyTimerItemStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


async def test_create_timer_item(client: httpx.AsyncClient) -> None:
    fire_at = now_epoch() + 120
    resp = await client.post(
        "/v1/timer-items",
        json={
            "task_type": "prompt",
            "fire_at": fire_at,
            "host_id": "host-a",
            "payload": {"session_id": "conv_1", "message": "ping"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "timer.item"
    assert body["task_type"] == "prompt"
    assert body["host_id"] == "host-a"
    assert body["state"] == "pending"
    assert body["payload"]["message"] == "ping"


async def test_host_due_claim_complete_flow(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    store = SqlAlchemyTimerItemStore(db_uri)
    item = store.create_item(
        _uid("due_route"),
        "prompt",
        now_epoch() - 1,
        "host-a",
        {"session_id": "conv_1", "message": "ping"},
    )
    host_headers = {HOST_AMBIENT_ID_HEADER: "host-a"}

    due_resp = await client.get("/v1/timer-items/due", headers=host_headers)
    assert due_resp.status_code == 200
    due_ids = {row["id"] for row in due_resp.json()["data"]}
    assert item.id in due_ids

    other_host = await client.get("/v1/timer-items/due", headers={HOST_AMBIENT_ID_HEADER: "host-b"})
    assert item.id not in {row["id"] for row in other_host.json()["data"]}

    claim_resp = await client.post(f"/v1/timer-items/{item.id}/claim", headers=host_headers)
    assert claim_resp.status_code == 200
    assert claim_resp.json()["state"] == "running"

    complete_resp = await client.post(
        f"/v1/timer-items/{item.id}/complete",
        headers=host_headers,
    )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["state"] == "done"


async def test_host_routes_require_header(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/timer-items/due")
    assert resp.status_code == 401
