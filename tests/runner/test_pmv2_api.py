"""Tests for the runner side of ``pmv2_api``."""

import json
from typing import Any

import httpx
import pytest

from omnigent.runner.identity import ORCHESTRATION_SOURCE_HEADER
from omnigent.runner.pmv2_api import execute_pmv2_api

SOURCE_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def _client(requests: list[httpx.Request], response: httpx.Response) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://server")


async def test_board_calls_are_sent_as_the_calling_session() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, httpx.Response(200, json={"id": "task_1"}))

    output = await execute_pmv2_api(
        {
            "method": "POST",
            "path": "/v1/agent-tasks",
            "body": {"title": "S3 retries"},
            "query": {"limit": 5},
        },
        client,
        SOURCE_ID,
    )

    assert json.loads(output) == {"id": "task_1"}
    [request] = requests
    assert (request.method, request.url.path, request.url.params["limit"]) == (
        "POST",
        "/v1/agent-tasks",
        "5",
    )
    assert json.loads(request.content) == {"title": "S3 retries"}
    assert request.headers[ORCHESTRATION_SOURCE_HEADER] == SOURCE_ID


@pytest.mark.parametrize(
    "args",
    [
        {"method": "GET", "path": "/v1/sessions"},
        {"method": "POST", "path": "/v1/hosts"},
        {"method": "GET", "path": "/v1/hosts/host_1/filesystem"},
        {"method": "GET", "path": "/v1/agent-tasks/../sessions"},
        {"method": "GET", "path": "/v1/agent-tasks/%2e%2e/sessions"},
        {"method": "GET", "path": "/v1/agent-tasks?state=active"},
        {"method": "GET", "path": "/v1/agent-tasksx"},
        {"method": "PUT", "path": "/v1/agent-tasks/task_1"},
        {"method": "get", "path": "/v1/agent-tasks"},
        {"method": "GET"},
    ],
)
async def test_other_routes_are_refused_before_any_request(args: dict[str, Any]) -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, httpx.Response(200, json={}))

    output = await execute_pmv2_api(args, client, SOURCE_ID)

    assert "error" in json.loads(output)
    assert requests == []


async def test_host_reads_and_server_errors_reach_the_model() -> None:
    requests: list[httpx.Request] = []
    hosts = _client(requests, httpx.Response(200, json={"data": []}))
    refusing = _client(requests, httpx.Response(404, json={"detail": "task not found"}))
    empty = _client(requests, httpx.Response(204))

    listed = await execute_pmv2_api({"method": "GET", "path": "/v1/hosts"}, hosts, SOURCE_ID)
    refused = await execute_pmv2_api(
        {"method": "GET", "path": "/v1/agent-tasks/task_1"}, refusing, SOURCE_ID
    )
    deleted = await execute_pmv2_api(
        {"method": "DELETE", "path": "/v1/agent-tasks/task_1"}, empty, SOURCE_ID
    )

    refusal = json.loads(refused)
    assert json.loads(listed) == {"data": []}
    assert (refusal["error"], "task not found" in refusal["details"]) == (
        "server returned 404",
        True,
    )
    assert json.loads(deleted) == {"status": "ok"}
