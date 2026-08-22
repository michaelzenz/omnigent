"""Runner coverage for in-place agent bundle updates."""

from __future__ import annotations

import asyncio

import pytest

from omnigent.runner import create_runner_app
from omnigent.spec.types import AgentSpec, ExecutorSpec
from tests.runner.conftest import (
    _FakeProcessManager,
    _runner_client,
    _ScriptedHarnessClient,
    _sse,
)
from tests.runner.helpers import NullServerClient


@pytest.mark.asyncio
async def test_new_agent_version_reloads_bundle_before_next_turn() -> None:
    """A version bump replaces cached instructions without recreating history."""
    prompt = "first prompt"
    resolve_calls: list[str] = []

    async def resolve(agent_id: str, session_id: str | None = None) -> AgentSpec:
        del session_id
        resolve_calls.append(agent_id)
        return AgentSpec(
            spec_version=1,
            name="role-agent",
            instructions=prompt,
            executor=ExecutorSpec(type="omnigent", config={"harness": "openai-agents"}),
        )

    harness = _ScriptedHarnessClient(
        [
            _sse({"type": "response.created", "response": {"id": "resp"}}),
            _sse({"type": "response.completed", "response": {"id": "resp"}}),
        ]
    )
    manager = _FakeProcessManager(harness)
    app = create_runner_app(
        process_manager=manager,  # type: ignore[arg-type]
        spec_resolver=resolve,
        server_client=NullServerClient(),  # type: ignore[arg-type]
    )

    async with _runner_client(app) as client:
        first = await client.post(
            "/v1/sessions/conv_role/events",
            json={
                "type": "message",
                "role": "user",
                "agent_id": "ag_role",
                "agent_version": 1,
                "content": [{"type": "input_text", "text": "first"}],
            },
        )
        assert first.status_code == 202
        for _ in range(200):
            if len(harness.posted_bodies) == 1 and not manager.has_active_turn("conv_role"):
                break
            await asyncio.sleep(0.01)

        prompt = "updated prompt"
        second = await client.post(
            "/v1/sessions/conv_role/events",
            json={
                "type": "message",
                "role": "user",
                "agent_id": "ag_role",
                "agent_version": 2,
                "content": [{"type": "input_text", "text": "second"}],
            },
        )
        assert second.status_code == 202
        for _ in range(200):
            if len(harness.posted_bodies) == 2:
                break
            await asyncio.sleep(0.01)

    assert resolve_calls == ["ag_role", "ag_role"]
    assert harness.posted_bodies[0]["instructions"] == "first prompt"
    assert harness.posted_bodies[1]["instructions"] == "updated prompt"
    assert manager.released == ["conv_role"]
