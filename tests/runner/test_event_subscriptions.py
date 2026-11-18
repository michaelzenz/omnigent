"""Tests for the runner side of ``event_subscriptions``."""

import json
from typing import Any

import httpx
import pytest

from omnigent.runner.event_subscriptions import execute_event_subscriptions
from omnigent.runner.identity import ORCHESTRATION_SOURCE_HEADER
from omnigent.runner.tool_dispatch import (
    build_native_relay_tool_schemas,
    execute_tool,
    should_dispatch_locally,
)
from omnigent.spec.types import AgentSpec

SOURCE_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
SUBSCRIPTIONS = f"/v1/sessions/{SOURCE_ID}/subscriptions"
PR = "https://github.com/example-org/repo/pull/12"
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
        await execute_event_subscriptions({"action": "types"}, client, SOURCE_ID),
        await execute_event_subscriptions({"action": "list"}, client, SOURCE_ID),
        await execute_event_subscriptions(
            {
                "action": "subscribe",
                "source_type": "github.pull_request",
                "source_key": PR,
                "note": "Opened it",
            },
            client,
            SOURCE_ID,
        ),
        await execute_event_subscriptions(
            {"action": "unsubscribe", "source_key": PR, "source_type": "ignored"},
            client,
            SOURCE_ID,
        ),
    ]

    assert [json.loads(output) for output in outputs] == [EMPTY] * 4
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/agent-tasks/source-types"),
        ("GET", SUBSCRIPTIONS),
        ("POST", SUBSCRIPTIONS),
        ("POST", f"{SUBSCRIPTIONS}/remove"),
    ]
    assert json.loads(requests[2].content) == {
        "source_type": "github.pull_request",
        "source_key": PR,
        "note": "Opened it",
    }
    assert json.loads(requests[3].content) == {"source_key": PR}
    assert {request.headers[ORCHESTRATION_SOURCE_HEADER] for request in requests} == {SOURCE_ID}


@pytest.mark.parametrize(
    ("args", "source_id"),
    [
        ({"action": "follow"}, SOURCE_ID),
        ({}, SOURCE_ID),
        ({"action": "subscribe", "source_type": "github.pull_request"}, SOURCE_ID),
        ({"action": "subscribe", "source_key": PR}, SOURCE_ID),
        ({"action": "subscribe", "source_type": "  ", "source_key": PR}, SOURCE_ID),
        ({"action": "unsubscribe", "source_key": "  "}, SOURCE_ID),
        ({"action": "list"}, None),
    ],
)
async def test_bad_calls_are_refused_before_any_request(
    args: dict[str, Any], source_id: str | None
) -> None:
    requests: list[httpx.Request] = []

    output = await execute_event_subscriptions(args, _client(requests), source_id)

    assert "error" in json.loads(output)
    assert requests == []


@pytest.mark.parametrize("spec", [AgentSpec(spec_version=1), None])
async def test_every_session_can_follow_sources_through_native_and_local_dispatch(
    spec: AgentSpec | None,
) -> None:
    schema = next(
        tool
        for tool in build_native_relay_tool_schemas(spec)
        if tool["name"] == "event_subscriptions"
    )
    requests: list[httpx.Request] = []

    async with _client(requests) as client:
        output = await execute_tool(
            tool_name="event_subscriptions",
            arguments=json.dumps({"action": "list"}),
            agent_spec=spec,
            conversation_id=SOURCE_ID,
            server_client=client,
        )

    assert set(schema["parameters"]["properties"]) == {
        "action",
        "source_type",
        "source_key",
        "note",
    }
    assert schema["parameters"]["required"] == ["action"]
    assert should_dispatch_locally("event_subscriptions")
    assert json.loads(output) == EMPTY
    assert [(request.method, request.url.path) for request in requests] == [("GET", SUBSCRIPTIONS)]
