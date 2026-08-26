"""Runner dispatch tests for project and workspace tools."""

from __future__ import annotations

import json

import httpx
import pytest

from omnigent.runner.tool_dispatch import (
    build_native_relay_tool_schemas,
    execute_tool,
)
from omnigent.spec.types import AgentSpec

# ── Native relay schema tests ─────────────────────────────────────


@pytest.mark.parametrize("spec", [AgentSpec(spec_version=1), None])
def test_native_relay_exposes_project_tools(spec: AgentSpec | None) -> None:
    """Project tools are always available; workspace tool is OmniHarness-only."""
    schemas = build_native_relay_tool_schemas(spec)
    names = {s["name"] for s in schemas}
    assert "sys_project_create" in names
    assert "sys_project_list" in names
    assert "sys_session_set_project" in names
    # Workspace tool is NOT registered for non-OmniHarness specs
    assert "sys_session_set_workspace" not in names


def test_native_relay_exposes_workspace_tool_for_omniharness() -> None:
    """The workspace tool appears only when the spec is OmniHarness."""
    from omnigent.execution_targets import OMNIHARNESS_AGENT_NAME

    spec = AgentSpec(spec_version=1, name=OMNIHARNESS_AGENT_NAME)
    schemas = build_native_relay_tool_schemas(spec)
    names = {s["name"] for s in schemas}
    assert "sys_session_set_workspace" in names


def test_project_create_schema() -> None:
    schemas = build_native_relay_tool_schemas(AgentSpec(spec_version=1))
    schema = next(s for s in schemas if s["name"] == "sys_project_create")
    assert schema["parameters"]["required"] == ["name"]
    assert schema["parameters"]["additionalProperties"] is False


def test_session_set_project_schema() -> None:
    schemas = build_native_relay_tool_schemas(AgentSpec(spec_version=1))
    schema = next(s for s in schemas if s["name"] == "sys_session_set_project")
    assert schema["parameters"]["required"] == ["project_id"]
    assert schema["parameters"]["additionalProperties"] is False


def test_session_set_workspace_schema() -> None:
    from omnigent.execution_targets import OMNIHARNESS_AGENT_NAME

    spec = AgentSpec(spec_version=1, name=OMNIHARNESS_AGENT_NAME)
    schemas = build_native_relay_tool_schemas(spec)
    schema = next(s for s in schemas if s["name"] == "sys_session_set_workspace")
    assert schema["parameters"]["required"] == ["workspace"]
    assert schema["parameters"]["additionalProperties"] is False


# ── Project create dispatch ───────────────────────────────────────


@pytest.mark.asyncio
async def test_project_create_dispatches_to_post_projects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "proj_abc",
                "object": "project",
                "name": "Managed Tables",
                "created_at": 1787430000,
                "updated_at": 1787430000,
                "config": {},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_project_create",
            arguments=json.dumps({"name": "Managed Tables"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )

    result = json.loads(output)
    assert result["id"] == "proj_abc"
    assert result["name"] == "Managed Tables"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/v1/projects"
    assert json.loads(requests[0].content) == {"name": "Managed Tables"}


@pytest.mark.asyncio
async def test_project_create_rejects_empty_name() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_project_create",
            arguments=json.dumps({"name": ""}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )
    assert "non-empty" in json.loads(output)["error"]


# ── Project list dispatch ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_list_dispatches_to_get_projects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "proj_abc",
                        "object": "project",
                        "name": "Managed Tables",
                        "created_at": 1787430000,
                        "updated_at": 1787430000,
                        "config": {},
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_project_list",
            arguments=json.dumps({}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )

    result = json.loads(output)
    assert result["object"] == "list"
    assert len(result["data"]) == 1
    assert result["data"][0]["id"] == "proj_abc"
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1/projects"


# ── Session set project dispatch ──────────────────────────────────


@pytest.mark.asyncio
async def test_set_project_dispatches_to_patch_session() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": "conv_current", "object": "session", "project_id": "proj_abc"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_set_project",
            arguments=json.dumps({"project_id": "proj_abc"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )

    result = json.loads(output)
    assert result["project_id"] == "proj_abc"
    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/v1/sessions/conv_current"
    assert json.loads(requests[0].content) == {"project_id": "proj_abc"}


@pytest.mark.asyncio
async def test_set_project_null_unfiles_via_empty_string() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": "conv_current", "object": "session", "project_id": None},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_set_project",
            arguments=json.dumps({"project_id": None}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )

    result = json.loads(output)
    assert result["project_id"] is None
    # null is translated to "" (the server's unfile sentinel)
    assert json.loads(requests[0].content) == {"project_id": ""}


# ── Session set workspace dispatch ────────────────────────────────


@pytest.mark.asyncio
async def test_set_workspace_dispatches_to_patch_session_and_updates_cache() -> None:
    requests: list[httpx.Request] = []
    workspace_updates: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "conv_current",
                "object": "session",
                "workspace": "/Users/me/projects/new",
            },
        )

    async def set_live_session_workspace(session_id: str, workspace: str) -> None:
        workspace_updates.append((session_id, workspace))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_set_workspace",
            arguments=json.dumps({"workspace": "/Users/me/projects/new"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
            set_live_session_workspace=set_live_session_workspace,
        )

    result = json.loads(output)
    assert result["workspace"] == "/Users/me/projects/new"
    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/v1/sessions/conv_current"
    assert json.loads(requests[0].content) == {"workspace": "/Users/me/projects/new"}
    # The live workspace cache callback was invoked
    assert workspace_updates == [("conv_current", "/Users/me/projects/new")]


@pytest.mark.asyncio
async def test_set_workspace_rejects_empty_path() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_set_workspace",
            arguments=json.dumps({"workspace": ""}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )
    assert "non-empty" in json.loads(output)["error"]


@pytest.mark.asyncio
async def test_set_workspace_without_callback_still_returns_server_response() -> None:
    """The async dispatch path has no live cache callback — still succeeds."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "conv_current",
                "object": "session",
                "workspace": "/Users/me/projects/new",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_set_workspace",
            arguments=json.dumps({"workspace": "/Users/me/projects/new"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
            # No set_live_session_workspace callback
        )

    result = json.loads(output)
    assert result["workspace"] == "/Users/me/projects/new"


# ── Error handling ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("sys_project_create", {"name": "test"}),
        ("sys_project_list", {}),
        ("sys_session_set_project", {"project_id": "proj_abc"}),
        ("sys_session_set_workspace", {"workspace": "/tmp"}),
    ],
)
async def test_tools_require_server_access(tool_name: str, args: dict) -> None:
    output = await execute_tool(
        tool_name=tool_name,
        arguments=json.dumps(args),
        server_client=None,
        conversation_id="conv_current",
        agent_spec=AgentSpec(spec_version=1),
    )
    assert "requires server access" in json.loads(output)["error"]
