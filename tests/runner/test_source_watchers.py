"""Tests for the runner side of ``source_watchers``."""

import json

import httpx
import pytest

from omnigent.runner.identity import ORCHESTRATION_SOURCE_HEADER
from omnigent.runner.source_watchers import execute_source_watchers

SOURCE_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
WATCHER_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
SOURCE_TYPES = "/v1/agent-tasks/source-types"
EMPTY = {"object": "list", "data": []}


def _client(requests: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=EMPTY)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://server")


async def test_each_action_calls_its_route_as_the_calling_session() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests)

    outputs = [
        await execute_source_watchers({"action": "list"}, client, SOURCE_ID),
        await execute_source_watchers(
            {"action": "assign", "source_type": " jira.issue ", "watcher_id": WATCHER_ID},
            client,
            SOURCE_ID,
        ),
        await execute_source_watchers(
            {"action": "unassign", "source_type": "jira.issue"}, client, SOURCE_ID
        ),
        await execute_source_watchers(
            {"action": "route", "source_type": "slack.conversation", "routing": "broker"},
            client,
            SOURCE_ID,
        ),
    ]

    assert [json.loads(output) for output in outputs] == [EMPTY] * 4
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", SOURCE_TYPES),
        ("POST", f"{SOURCE_TYPES}/jira.issue/watcher"),
        ("DELETE", f"{SOURCE_TYPES}/jira.issue/watcher"),
        ("POST", f"{SOURCE_TYPES}/slack.conversation/routing"),
    ]
    assert json.loads(requests[1].content) == {"watcher_id": WATCHER_ID}
    assert json.loads(requests[3].content) == {"routing": "broker"}
    assert {request.headers[ORCHESTRATION_SOURCE_HEADER] for request in requests} == {SOURCE_ID}


@pytest.mark.parametrize(
    ("args", "source_id"),
    [
        ({"action": "claim"}, SOURCE_ID),
        ({"action": "assign", "watcher_id": WATCHER_ID}, SOURCE_ID),
        ({"action": "assign", "source_type": "jira.issue"}, SOURCE_ID),
        ({"action": "unassign", "source_type": "  "}, SOURCE_ID),
        ({"action": "route", "source_type": "slack.conversation"}, SOURCE_ID),
        ({"action": "route", "source_type": "slack.conversation", "routing": "all"}, SOURCE_ID),
        ({"action": "list"}, None),
    ],
)
async def test_a_call_without_what_it_needs_sends_nothing(
    args: dict[str, str], source_id: str | None
) -> None:
    requests: list[httpx.Request] = []

    output = json.loads(await execute_source_watchers(args, _client(requests), source_id))

    assert "error" in output
    assert requests == []
