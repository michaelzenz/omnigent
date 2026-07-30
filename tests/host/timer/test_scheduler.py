"""Tests for the host timer scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from omnigent.host.timer.context import TimerContext
from omnigent.host.timer.scheduler import TimerScheduler


@dataclass
class _FakeHandler:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: bool = False

    async def handle(self, ctx: TimerContext, *, item_id: str, payload: dict[str, Any]) -> None:
        self.calls.append((item_id, payload))
        if self.fail:
            raise RuntimeError("handler failed")


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.claimed: set[str] = set()
        self.completed: set[str] = set()
        self.failed: set[str] = set()
        self.dispatch_calls: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/timer-items/due":
            return httpx.Response(200, json={"object": "list", "data": list(self.items)})
        if path.endswith("/claim"):
            item_id = path.rsplit("/", 2)[-2]
            if item_id in self.claimed:
                return httpx.Response(404, json={"error": "not claimable"})
            self.claimed.add(item_id)
            item = next(row for row in self.items if row["id"] == item_id)
            claimed = dict(item)
            claimed["state"] = "running"
            return httpx.Response(200, json=claimed)
        if path.endswith("/complete"):
            item_id = path.rsplit("/", 2)[-2]
            self.completed.add(item_id)
            return httpx.Response(200, json={"id": item_id, "state": "done"})
        if path.endswith("/fail"):
            item_id = path.rsplit("/", 2)[-2]
            self.failed.add(item_id)
            return httpx.Response(200, json={"id": item_id, "state": "failed"})
        if path == "/v1/timer-items/dispatch-prompt":
            import json

            self.dispatch_calls.append(json.loads(request.content.decode()))
            return httpx.Response(200, json={"delivered": True})
        return httpx.Response(404, json={"error": "not found"})


@pytest.mark.asyncio
async def test_timer_scheduler_claims_and_completes_items() -> None:
    handler = _FakeHandler()
    transport = _FakeTransport(
        items=[
            {
                "id": "timer123",
                "task_type": "prompt",
                "payload": {"session_id": "conv_1", "message": "wake"},
            }
        ],
    )
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Omnigent-Host-Ambient-Id": "host-a"},
    )
    scheduler = TimerScheduler(
        server_url="http://test",
        host_id="host-a",
        handlers={"prompt": handler},
    )
    scheduler._client = client
    scheduler._ctx = TimerContext(
        server_url="http://test",
        host_id="host-a",
        client=client,
    )

    await scheduler.tick_once()

    assert handler.calls == [("timer123", {"session_id": "conv_1", "message": "wake"})]
    assert transport.claimed == {"timer123"}
    assert transport.completed == {"timer123"}
    await client.aclose()


@pytest.mark.asyncio
async def test_timer_scheduler_marks_unknown_handler_failed() -> None:
    transport = _FakeTransport(
        items=[{"id": "timer456", "task_type": "missing", "payload": {}}],
    )
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    scheduler = TimerScheduler(server_url="http://test", host_id="host-a", handlers={})
    scheduler._client = client
    scheduler._ctx = TimerContext(server_url="http://test", host_id="host-a", client=client)

    await scheduler.tick_once()

    assert transport.failed == {"timer456"}
    await client.aclose()
