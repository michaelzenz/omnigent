"""Tests for task worker dispatch helpers."""

from __future__ import annotations

from omnigent.agent_tasks.dispatch import compose_worker_instructions, resolve_dispatch_params
from omnigent.entities.task_role_profile import TaskRoleProfile


def test_compose_worker_instructions_merges_note() -> None:
    merged = compose_worker_instructions(
        instructions="Fix the test.",
        internal_note="PR #42 failed on lint.",
    )
    assert "Fix the test." in merged
    assert "PR #42 failed on lint." in merged
    assert "## Context" in merged


def test_compose_worker_instructions_note_only() -> None:
    assert compose_worker_instructions(instructions=None, internal_note="CI log excerpt") == (
        "CI log excerpt"
    )


def test_resolve_dispatch_params_includes_internal_note() -> None:
    """Params resolve to worker instructions; placement fields are ignored."""
    params = resolve_dispatch_params(
        payload={
            "title": "Fix CI",
            "instructions": "Run tests",
            "internal_note": "Failed on main at abc123",
            "host_id": "host-a",
            "workspace": "/tmp/ws",
            "harness": "cursor",
            "model": "composer-2.5",
        },
        role_profile=TaskRoleProfile(
            role="manager:default",
            kind="manager",
            agent_profile_id="agent-1",
            created_at=1,
        ),
    )
    assert "Failed on main at abc123" in params.instructions
    assert "Run tests" in params.instructions
    # DispatchParams carries instructions only — workers are placed by
    # their provider snapshot, not by the dispatch payload.
    assert params.instructions
