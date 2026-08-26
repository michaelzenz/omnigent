"""Tests for the ``puppygarden_api`` task-API proxy tool.

Covers the runner-side half:

- ``_execute_puppygarden_api_tool``: proxies the correct HTTP verb + path +
  body/query over ``server_client``, validates the path against the task-API
  prefixes (rejecting non-task paths), and translates a server 4xx/5xx and a
  missing client into clean error JSON.
- Registration: ``puppygarden_api`` is always registered by ``ToolManager``
  (no spec opt-in) and is a member of the local-dispatch and native-relay
  tool sets.
"""

from __future__ import annotations

import json

import pytest

from omnigent.runner.tool_dispatch import (
    _ALL_LOCAL_TOOLS,
    _NATIVE_RELAY_BUILTIN_TOOLS,
    _PUPPYGARDEN_API_TOOLS,
    _execute_puppygarden_api_tool,
)
from omnigent.tools.builtins.puppygarden_api import PuppyGardenApiTool

_TOOL_NAME = PuppyGardenApiTool.name()


class _Resp:
    def __init__(self, *, status_code: int = 200, body: object | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = b"x" if body is not None else b""

    @property
    def text(self) -> str:
        return json.dumps(self._body)

    def json(self) -> object:
        return self._body


class _RecordingClient:
    """Records the verb/url/kwargs of each request and returns a scripted response."""

    def __init__(self, response: _Resp | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self._response = response or _Resp(body={"id": "t1"})

    async def request(self, method: str, url: str, **kwargs: object) -> _Resp:
        self.calls.append((method, url, dict(kwargs)))
        return self._response


@pytest.mark.asyncio
async def test_get_proxies_path() -> None:
    client = _RecordingClient(_Resp(body={"object": "task", "id": "abc"}))
    out = await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "GET", "path": "/v1/agent-tasks/abc"}),
        server_client=client,
    )
    method, url, _ = client.calls[0]
    assert (method, url) == ("GET", "/v1/agent-tasks/abc")
    assert json.loads(out)["id"] == "abc"


@pytest.mark.asyncio
async def test_post_passes_body() -> None:
    client = _RecordingClient(_Resp(body={"id": "t1"}))
    await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps(
            {
                "method": "POST",
                "path": "/v1/agent-tasks/batch",
                "body": {"task_ids": ["a", "b"]},
            }
        ),
        server_client=client,
    )
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("POST", "/v1/agent-tasks/batch")
    assert kwargs["json"] == {"task_ids": ["a", "b"]}


@pytest.mark.asyncio
async def test_get_passes_query() -> None:
    client = _RecordingClient(_Resp(body={"data": []}))
    await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps(
            {
                "method": "GET",
                "path": "/v1/agent-tasks/roles/profiles",
                "query": {"kind": "worker"},
            }
        ),
        server_client=client,
    )
    _, _, kwargs = client.calls[0]
    assert kwargs["params"] == {"kind": "worker"}
    assert "json" not in kwargs  # GET never sends a body


@pytest.mark.asyncio
async def test_patch_passes_body() -> None:
    client = _RecordingClient(_Resp(body={"id": "t1", "state": "idle"}))
    await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "PATCH", "path": "/v1/agent-tasks/t1", "body": {"state": "idle"}}),
        server_client=client,
    )
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("PATCH", "/v1/agent-tasks/t1")
    assert kwargs["json"] == {"state": "idle"}


@pytest.mark.asyncio
async def test_put_passes_body() -> None:
    client = _RecordingClient(_Resp(body={"role": "manager:default"}))
    await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps(
            {
                "method": "PUT",
                "path": "/v1/agent-tasks/roles/manager:default/profile",
                "body": {"host_id": "host-1"},
            }
        ),
        server_client=client,
    )
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("PUT", "/v1/agent-tasks/roles/manager:default/profile")
    assert kwargs["json"] == {"host_id": "host-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/agent-tasks",
        "/v1/agent-queues",
        "/v1/agent-queue-items/item-1",
        "/v1/fyi-clusters/cluster-1/resolve",
        "/v1/session-watcher/update",
        "/v1/task-events",
        "/v1/task-items/item-1",
        "/v1/task-workers/worker-1/initialize",
        "/v1/worker-providers",
        "/v1/worker-providers/provider-1",
    ],
)
async def test_proxies_every_puppygarden_api_family(path: str) -> None:
    client = _RecordingClient()
    await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "GET", "path": path}),
        server_client=client,
    )
    assert client.calls[0][1] == path


@pytest.mark.asyncio
async def test_delete_handles_no_content() -> None:
    client = _RecordingClient(_Resp(status_code=204, body=None))
    out = await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "DELETE", "path": "/v1/task-items/abc"}),
        server_client=client,
    )
    assert json.loads(out) == {"status": "ok"}


@pytest.mark.asyncio
async def test_rejects_non_task_path() -> None:
    client = _RecordingClient()
    out = await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "GET", "path": "/v1/sessions/abc"}),
        server_client=client,
    )
    assert "only proxies PuppyGarden API paths" in json.loads(out)["error"]
    assert client.calls == []  # never hit the server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/agent-tasks-admin",
        "/v1/agent-tasks/../sessions",
        "/v1/agent-tasks/%2e%2e/sessions",
    ],
)
async def test_rejects_paths_outside_exact_api_families(path: str) -> None:
    client = _RecordingClient()
    out = await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "GET", "path": path}),
        server_client=client,
    )
    assert "only proxies PuppyGarden API paths" in json.loads(out)["error"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_rejects_bad_method() -> None:
    client = _RecordingClient()
    out = await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "TRACE", "path": "/v1/agent-tasks/abc"}),
        server_client=client,
    )
    assert "method" in json.loads(out)["error"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_rejects_missing_path() -> None:
    client = _RecordingClient()
    out = await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "GET"}),
        server_client=client,
    )
    assert "path" in json.loads(out)["error"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_server_error_becomes_clean_json() -> None:
    client = _RecordingClient(_Resp(status_code=400, body={"error": {"message": "bad"}}))
    out = await _execute_puppygarden_api_tool(
        _TOOL_NAME,
        json.dumps({"method": "POST", "path": "/v1/agent-tasks", "body": {}}),
        server_client=client,
    )
    assert "server returned 400" in json.loads(out)["error"]


@pytest.mark.asyncio
async def test_no_server_client_errors() -> None:
    out = await _execute_puppygarden_api_tool(
        _TOOL_NAME, json.dumps({"method": "GET", "path": "/v1/agent-tasks"}), server_client=None
    )
    assert "requires server access" in json.loads(out)["error"]


def test_tool_registered_without_spec_optin() -> None:
    from omnigent.spec.types import AgentSpec
    from omnigent.tools.manager import ToolManager

    mgr = ToolManager(AgentSpec(spec_version=1))
    names = {s["function"]["name"] for s in mgr.get_tool_schemas()}
    assert _TOOL_NAME in names


def test_tool_in_dispatch_and_relay_sets() -> None:
    assert {_TOOL_NAME} == _PUPPYGARDEN_API_TOOLS
    assert _TOOL_NAME in _ALL_LOCAL_TOOLS
    assert _TOOL_NAME in _NATIVE_RELAY_BUILTIN_TOOLS


def test_schema_requires_method_and_path() -> None:
    schema = PuppyGardenApiTool().get_schema()["function"]["parameters"]
    assert set(schema["required"]) == {"method", "path"}
    assert schema["properties"]["method"]["enum"] == ["GET", "POST", "PUT", "PATCH", "DELETE"]
