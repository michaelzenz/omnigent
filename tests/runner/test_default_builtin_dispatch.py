"""Runner dispatch coverage for default portable builtins."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from omnigent.runner.tool_dispatch import (
    _NATIVE_RELAY_BUILTIN_TOOLS,
    execute_tool,
    should_dispatch_locally,
)
from omnigent.spec.types import AgentSpec


def test_default_builtins_are_runner_and_native_relay_tools() -> None:
    """Default builtins must work for SDK and native harnesses."""
    names = {
        "web_fetch",
        "upload_file",
        "list_files",
        "download_file",
        "search_conversations",
        "export_agent",
        "sys_timer_set",
        "sys_timer_cancel",
    }

    assert all(should_dispatch_locally(name) for name in names)
    assert names <= _NATIVE_RELAY_BUILTIN_TOOLS


@pytest.mark.asyncio
async def test_search_conversations_uses_server_session_search() -> None:
    """Conversation search delegates to the permission-aware server endpoint."""
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "conv_match", "title": "Tool self-test"}]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as server_client:
        output = await execute_tool(
            tool_name="search_conversations",
            arguments=json.dumps({"query": "tool self-test", "limit": 3}),
            server_client=server_client,
        )

    assert json.loads(output) == {"results": [{"id": "conv_match", "title": "Tool self-test"}]}
    assert captured[0].url.path == "/v1/sessions"
    assert captured[0].url.params["search_query"] == "tool self-test"
    assert captured[0].url.params["limit"] == "3"
    assert captured[0].url.params["kind"] == "any"


@pytest.mark.asyncio
async def test_export_agent_executes_through_tool_manager(tmp_path: Path) -> None:
    """The exported default builtin reaches its runner-local implementation."""
    conversation_id = "conv_export"
    source = tmp_path / conversation_id / "generated-agent"
    source.mkdir(parents=True)
    (source / "config.yaml").write_text("spec_version: 1\n")
    target = tmp_path / "exported-agent"

    output = await execute_tool(
        tool_name="export_agent",
        arguments=json.dumps(
            {
                "source": "generated-agent",
                "target": str(target),
            }
        ),
        agent_spec=AgentSpec(spec_version=1),
        conversation_id=conversation_id,
        runner_workspace=tmp_path,
    )

    assert output == f"Exported agent to {target}"
    assert (target / "config.yaml").read_text() == "spec_version: 1\n"
