"""Tests for task bootstrap parameter resolution."""

from __future__ import annotations

import pytest

from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError


def _profile(*, harness: str, model: str | None) -> TaskRoleProfile:
    return TaskRoleProfile(
        role="manager:default",
        kind="manager",
        agent_profile_id="agent",
        harness=harness,
        model=model,
        created_at=0,
        host_id="host",
        workspace="~/",
        updated_at=None,
    )


def _resolve(profile: TaskRoleProfile | None, **overrides: str | None):
    return resolve_bootstrap_params(
        host_id="host",
        workspace="~/",
        model=overrides.get("model"),
        role_profile=profile,
    )


def test_cleared_model_stays_unset() -> None:
    """A harness that picks its own model (e.g. Codex) launches without one."""
    params = _resolve(_profile(harness="codex-native", model=None))
    assert params.harness == "codex-native"
    assert params.model is None


def test_blank_model_is_normalized_to_none() -> None:
    params = _resolve(_profile(harness="codex-native", model=""))
    assert params.model is None


def test_claude_cli_alias_survives() -> None:
    """Claude Code's version-agnostic aliases are valid ``--model`` values."""
    params = _resolve(_profile(harness="claude-native", model="sonnet"))
    assert params.harness == "claude-native"
    assert params.model == "sonnet"


def test_profile_model_passes_through() -> None:
    params = _resolve(_profile(harness="cursor-native", model="composer-2.5"))
    assert params.model == "composer-2.5"


def test_explicit_model_overrides_profile() -> None:
    params = _resolve(_profile(harness="cursor-native", model="composer-2.5"), model="opus")
    assert params.model == "opus"


def test_missing_agent_profile_is_rejected() -> None:
    """A role without an agent profile cannot name what to launch."""
    with pytest.raises(OmnigentError) as exc:
        _resolve(None)
    assert exc.value.code == ErrorCode.INVALID_INPUT


def test_manager_session_request_carries_role_label() -> None:
    """Manager sessions are labeled as background roles for badge suppression."""
    import asyncio

    from omnigent.agent_tasks.bootstrap import _session_request_for_manager
    from omnigent.agent_tasks.session_labels import MANAGER_ROLE_VALUE, ROLE_LABEL

    manager = type(
        "Manager",
        (),
        {
            "id": "mgr1",
            "owner_user_id": "__anonymous__",
            "role_key": "manager:default",
            "agent_profile_id": "agent-1",
            "prompt_profile_id": None,
            "title": "Release manager",
            "host_id": "host-1",
            "workspace": "~/",
            "harness": "codex-native",
            "model": None,
        },
    )()

    request = asyncio.run(
        _session_request_for_manager(manager, app_state=type("S", (), {"project_store": None})())
    )
    assert request.labels[ROLE_LABEL] == MANAGER_ROLE_VALUE
