"""Tests for the runner side of ``watch_items``."""

import json
from typing import Any

import httpx
import pytest

from omnigent.runner.identity import ORCHESTRATION_SOURCE_HEADER
from omnigent.runner.watch_items import execute_watch_items

SOURCE_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
ROUTE = "/v1/agent-tasks/watchers/self/items"
EMPTY = {"object": "list", "data": [], "total": 0}
ITEM = {"source": "github", "source_key": "https://example.com/pr/1", "offsets": {"state": "open"}}


def _client(requests: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=EMPTY)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://server")


async def test_each_action_calls_its_route_as_the_calling_session() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests)

    outputs = [
        await execute_watch_items(
            {"action": "list", "status": "all", "source": "github", "after": "abc", "items": None},
            client,
            SOURCE_ID,
        ),
        await execute_watch_items({"action": "observe", "items": [ITEM]}, client, SOURCE_ID),
        await execute_watch_items({"action": "done", "items": [ITEM]}, client, SOURCE_ID),
    ]

    assert [json.loads(output) for output in outputs] == [EMPTY] * 3
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", ROUTE),
        ("POST", f"{ROUTE}/observe"),
        ("POST", f"{ROUTE}/done"),
    ]
    assert dict(requests[0].url.params) == {"status": "all", "source": "github", "after": "abc"}
    assert json.loads(requests[1].content) == {"items": [ITEM]}
    # Only an item's key counts for done.
    assert json.loads(requests[2].content) == {
        "items": [{"source": "github", "source_key": "https://example.com/pr/1"}]
    }
    assert {request.headers[ORCHESTRATION_SOURCE_HEADER] for request in requests} == {SOURCE_ID}


@pytest.mark.parametrize(
    ("args", "source_id"),
    [
        ({"action": "delete"}, SOURCE_ID),
        ({}, SOURCE_ID),
        ({"action": "observe"}, SOURCE_ID),
        ({"action": "done", "items": []}, SOURCE_ID),
        ({"action": "list"}, None),
    ],
)
async def test_bad_calls_are_refused_before_any_request(
    args: dict[str, Any], source_id: str | None
) -> None:
    requests: list[httpx.Request] = []

    output = await execute_watch_items(args, _client(requests), source_id)

    assert "error" in json.loads(output)
    assert requests == []
