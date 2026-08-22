"""Launch validation for profiles without pinned executor settings."""

from types import SimpleNamespace

import pytest

from omnigent.entities.agent import Agent
from omnigent.errors import OmnigentError
from omnigent.server.routes._sessions.orchestration import (
    _require_generic_profile_launch_config,
)
from omnigent.spec.types import AgentSpec


class _AgentCache:
    def load(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(spec=AgentSpec(spec_version=1, name="generic-profile"))


def _profile() -> Agent:
    return Agent(
        id="ag_profile",
        created_at=1,
        name="generic-profile",
        bundle_location="profiles/generic",
    )


def test_generic_profile_requires_launch_harness_and_model() -> None:
    with pytest.raises(OmnigentError, match="choose harness and model in launch settings"):
        _require_generic_profile_launch_config(
            _profile(),
            _AgentCache(),  # type: ignore[arg-type]
            harness_override=None,
            model_override=None,
        )


def test_generic_profile_accepts_complete_launch_settings() -> None:
    _require_generic_profile_launch_config(
        _profile(),
        _AgentCache(),  # type: ignore[arg-type]
        harness_override="openai-agents",
        model_override="databricks-glm-5-2",
    )
