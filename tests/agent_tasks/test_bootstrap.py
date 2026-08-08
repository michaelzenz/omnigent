"""Tests for task bootstrap parameter resolution."""

from __future__ import annotations

from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.entities.task_role_profile import UserTaskRoleProfile


def _profile(*, harness: str, model: str | None) -> UserTaskRoleProfile:
    return UserTaskRoleProfile(
        user_id="user",
        role="manager:default",
        agent_profile_id="agent",
        harness=harness,
        model=model,
        created_at=0,
        conversation_id=None,
        host_id="host",
        workspace="~/",
        updated_at=None,
    )


def _resolve(profile: UserTaskRoleProfile | None, **overrides: str | None):
    return resolve_bootstrap_params(
        host_id="host",
        workspace="~/",
        harness=overrides.get("harness"),
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


def test_missing_profile_defers_model_to_harness() -> None:
    params = _resolve(None)
    assert params.harness == "cursor-native"
    assert params.model is None
