"""Tests for the runner side of ``system_notice``."""

import json
from typing import Any

import httpx
import pytest

from omnigent.runner.identity import ORCHESTRATION_SOURCE_HEADER
from omnigent.runner.system_notice import execute_system_notice

SOURCE_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
ROUTE = "/v1/agent-tasks/system-notice"
NO_NOTICE = {"object": "agent.task.board.system_notice", "notice": None}


def _client(requests: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=NO_NOTICE)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://server")


async def test_each_action_calls_its_route_as_the_calling_session() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests)
    actions = [
        {"id": "confirm", "inputs": [{"id": "delete_a", "kind": "toggle", "group": "Host one"}]}
    ]

    outputs = [
        await execute_system_notice(
            {"action": "publish", "title": "Cleanup", "body": None, "actions": actions},
            client,
            SOURCE_ID,
        ),
        await execute_system_notice({"action": "get"}, client, SOURCE_ID),
        await execute_system_notice({"action": "clear"}, client, SOURCE_ID),
    ]

    assert [json.loads(output) for output in outputs] == [NO_NOTICE] * 3
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", ROUTE),
        ("GET", ROUTE),
        ("POST", f"{ROUTE}/dismiss"),
    ]
    assert json.loads(requests[0].content) == {"title": "Cleanup", "actions": actions}
    assert {request.headers[ORCHESTRATION_SOURCE_HEADER] for request in requests} == {SOURCE_ID}


async def test_each_staging_action_calls_its_draft_route() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests)
    change = {"kind": "delete_session", "session": SOURCE_ID, "summary": "Idle"}

    for args in [
        {"action": "stage", "change": change},
        {"action": "unstage", "change_id": f"delete_session__{SOURCE_ID}"},
        {"action": "check"},
        {"action": "commit", "title": "Idle sessions"},
        {"action": "commit"},
        {"action": "discard"},
    ]:
        await execute_system_notice(args, client, SOURCE_ID)

    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", f"{ROUTE}/draft/changes"),
        ("DELETE", f"{ROUTE}/draft/changes/delete_session__{SOURCE_ID}"),
        ("GET", f"{ROUTE}/draft"),
        ("POST", f"{ROUTE}/draft/commit"),
        ("POST", f"{ROUTE}/draft/commit"),
        ("DELETE", f"{ROUTE}/draft"),
    ]
    assert [json.loads(requests[index].content) for index in (0, 3, 4)] == [
        {"change": change},
        {"title": "Idle sessions"},
        {},
    ]


@pytest.mark.parametrize(
    ("args", "source_id"),
    [
        ({"action": "delete"}, SOURCE_ID),
        ({}, SOURCE_ID),
        ({"action": "get"}, None),
        ({"action": "stage"}, SOURCE_ID),
        ({"action": "unstage", "change_id": "../draft"}, SOURCE_ID),
    ],
)
async def test_bad_calls_are_refused_before_any_request(
    args: dict[str, Any], source_id: str | None
) -> None:
    requests: list[httpx.Request] = []

    output = await execute_system_notice(args, _client(requests), source_id)

    assert "error" in json.loads(output)
    assert requests == []
