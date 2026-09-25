"""Runner dispatch tests for the worktree creation and relocation tools."""

from __future__ import annotations

import json

import httpx
import pytest

from omnigent.runner.tool_dispatch import (
    build_native_relay_tool_schemas,
    execute_tool,
)
from omnigent.spec.types import AgentSpec

# ── Schema tests ──────────────────────────────────────────────────


def test_native_relay_exposes_worktree_and_relocate_tools() -> None:
    """Both tools ride the native relay surface for every harness."""
    schemas = build_native_relay_tool_schemas(AgentSpec(spec_version=1))
    names = {s["name"] for s in schemas}
    assert "sys_session_create_worktree" in names
    assert "sys_session_relocate" in names


def test_session_create_worktree_schema() -> None:
    schemas = build_native_relay_tool_schemas(AgentSpec(spec_version=1))
    schema = next(s for s in schemas if s["name"] == "sys_session_create_worktree")
    assert schema["parameters"]["required"] == ["host", "parent_folder", "target_branch"]
    assert schema["parameters"]["additionalProperties"] is False
    props = schema["parameters"]["properties"]
    assert set(props) == {"host", "parent_folder", "parent_branch", "target_branch"}


def test_session_relocate_schema() -> None:
    schemas = build_native_relay_tool_schemas(AgentSpec(spec_version=1))
    schema = next(s for s in schemas if s["name"] == "sys_session_relocate")
    assert schema["parameters"]["required"] == ["host", "folder"]
    assert schema["parameters"]["additionalProperties"] is False


# ── Worktree create + relocate dispatch ───────────────────────────


@pytest.mark.asyncio
async def test_create_worktree_unbinds_then_launches_managed() -> None:
    """The tool unbinds, then launches with git.managed on the parent folder."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "conv_current"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "conv_current",
                    "workspace": "/Users/me/repo-wt/feature-login",
                    "git_branch": "feature/login",
                },
            )
        return httpx.Response(200, json={"runner_id": "runner_new", "status": "launching"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_create_worktree",
            arguments=json.dumps(
                {
                    "host": "host_abc",
                    "parent_folder": "/Users/me/repo",
                    "parent_branch": "main",
                    "target_branch": "feature/login",
                }
            ),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )

    result = json.loads(output)
    assert result["relocated"] is True
    assert result["host"] == "host_abc"
    assert result["runner_id"] == "runner_new"
    assert [r.method for r in requests] == ["PATCH", "POST", "GET"]
    assert requests[0].url.path == "/v1/sessions/conv_current"
    assert json.loads(requests[0].content) == {
        "runner_id": "",
        "model_override": "default",
        "silent": True,
    }
    # The launch targets the PARENT folder with managed git options — the
    # server creates the leased worktree and binds the runner inside it.
    assert requests[1].url.path == "/v1/hosts/host_abc/runners"
    assert json.loads(requests[1].content) == {
        "session_id": "conv_current",
        "workspace": "/Users/me/repo",
        "git": {
            "branch_name": "feature/login",
            "base_branch": "main",
            "managed": True,
        },
    }
    # The snapshot read reports where the session landed.
    assert requests[2].url.path == "/v1/sessions/conv_current"
    assert result["workspace"] == "/Users/me/repo-wt/feature-login"
    assert result["branch"] == "feature/login"


@pytest.mark.asyncio
async def test_create_worktree_omits_base_branch_when_absent() -> None:
    """parent_branch omitted → no base_branch in the git options."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "conv_current"})
        return httpx.Response(200, json={"runner_id": "runner_new", "status": "launching"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_create_worktree",
            arguments=json.dumps(
                {"host": "host_abc", "parent_folder": "/Users/me/repo", "target_branch": "spike"}
            ),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )

    assert json.loads(output)["relocated"] is True
    assert json.loads(requests[1].content)["git"] == {
        "branch_name": "spike",
        "managed": True,
    }


@pytest.mark.asyncio
async def test_create_worktree_unbind_failure_aborts_before_launch() -> None:
    """A failed unbind stops the flow — no launch request is made."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"detail": "session not found"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_create_worktree",
            arguments=json.dumps(
                {"host": "host_abc", "parent_folder": "/repo", "target_branch": "b"}
            ),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )
    result = json.loads(output)
    assert "unbind returned 404" in result["error"]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_create_worktree_launch_failure_reports_unbound_state() -> None:
    """A failed launch leaves the session unbound — the output says so."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "conv_current"})
        return httpx.Response(400, json={"detail": "branch already exists"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_create_worktree",
            arguments=json.dumps(
                {"host": "host_abc", "parent_folder": "/repo", "target_branch": "dup"}
            ),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )
    result = json.loads(output)
    assert "launch returned 400" in result["error"]
    assert "unbound" in result["state"]
    assert "branch already exists" in result["detail"]
    assert [r.method for r in requests] == ["PATCH", "POST"]


@pytest.mark.asyncio
async def test_create_worktree_requires_args() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        base_url="http://server",
    ) as server_client:
        for args in (
            {"parent_folder": "/r", "target_branch": "b"},
            {"host": "h", "target_branch": "b"},
            {"host": "h", "parent_folder": "/r"},
        ):
            output = await execute_tool(
                tool_name="sys_session_create_worktree",
                arguments=json.dumps(args),
                server_client=server_client,
                conversation_id="conv_current",
                agent_spec=AgentSpec(spec_version=1),
            )
            assert "error" in json.loads(output)


# ── Relocation dispatch ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_relocate_unbinds_then_launches() -> None:
    """The tool PATCHes the runner binding away, then launches on the host."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "conv_current"})
        return httpx.Response(200, json={"runner_id": "runner_new", "status": "launching"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_relocate",
            arguments=json.dumps({"host": "host_new", "folder": "/Users/me/repo-wt"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )

    result = json.loads(output)
    assert result == {
        "relocated": True,
        "host": "host_new",
        "folder": "/Users/me/repo-wt",
        "runner_id": "runner_new",
        "status": "launching",
    }
    assert [r.method for r in requests] == ["PATCH", "POST"]
    assert requests[0].url.path == "/v1/sessions/conv_current"
    assert json.loads(requests[0].content) == {
        "runner_id": "",
        "model_override": "default",
        "silent": True,
    }
    assert requests[1].url.path == "/v1/hosts/host_new/runners"
    assert json.loads(requests[1].content) == {
        "session_id": "conv_current",
        "workspace": "/Users/me/repo-wt",
    }


@pytest.mark.asyncio
async def test_relocate_unbind_failure_aborts_before_launch() -> None:
    """A failed unbind stops the flow — no launch request is made."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"detail": "session not found"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_relocate",
            arguments=json.dumps({"host": "host_new", "folder": "/wt"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )
    result = json.loads(output)
    assert "unbind returned 404" in result["error"]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_relocate_launch_failure_reports_unbound_state() -> None:
    """A failed launch leaves the session unbound — the output says so."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "conv_current"})
        return httpx.Response(400, json={"detail": "workspace is outside the agent's path"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="sys_session_relocate",
            arguments=json.dumps({"host": "host_new", "folder": "/outside/boundary"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )
    result = json.loads(output)
    assert "launch returned 400" in result["error"]
    assert "unbound" in result["state"]
    assert "outside the agent's path" in result["detail"]
    assert [r.method for r in requests] == ["PATCH", "POST"]


@pytest.mark.asyncio
async def test_relocate_requires_args_and_session() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        base_url="http://server",
    ) as server_client:
        no_session = await execute_tool(
            tool_name="sys_session_relocate",
            arguments=json.dumps({"host": "h", "folder": "/wt"}),
            server_client=server_client,
            conversation_id=None,
            agent_spec=AgentSpec(spec_version=1),
        )
        assert "session id" in json.loads(no_session)["error"]
        no_host = await execute_tool(
            tool_name="sys_session_relocate",
            arguments=json.dumps({"folder": "/wt"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )
        assert "non-empty 'host'" in json.loads(no_host)["error"]
        no_folder = await execute_tool(
            tool_name="sys_session_relocate",
            arguments=json.dumps({"host": "h"}),
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )
        assert "non-empty 'folder'" in json.loads(no_folder)["error"]
