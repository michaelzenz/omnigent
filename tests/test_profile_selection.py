import json
from types import SimpleNamespace

import pytest

from omnigent.entities.agent import Agent
from omnigent.errors import OmnigentError
from omnigent.profile_selection import (
    apply_prompt_profile,
    auto_select_prompt_profile,
    is_prompt_profile,
    list_prompt_profiles,
    load_prompt_profile_instructions,
)
from omnigent.spec.types import AgentSpec


class _AgentStore:
    def __init__(self, agent: Agent | None) -> None:
        self.agent = agent

    def get(self, agent_id: str) -> Agent | None:
        return self.agent if self.agent is not None and self.agent.id == agent_id else None


class _AgentCache:
    def load(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            spec=AgentSpec(
                spec_version=1,
                name="focused",
                instructions="Focus only on the selected task.",
            )
        )


def _profile(**overrides: object) -> Agent:
    values: dict[str, object] = {
        "id": "ag_profile",
        "created_at": 1,
        "name": "focused",
        "bundle_location": "profiles/focused",
        "auto_select_enabled": True,
    }
    values.update(overrides)
    return Agent(**values)  # type: ignore[arg-type]


def test_load_prompt_profile_returns_only_instructions() -> None:
    instructions = load_prompt_profile_instructions(
        "ag_profile",
        _AgentStore(_profile()),  # type: ignore[arg-type]
        _AgentCache(),  # type: ignore[arg-type]
        require_selectable=True,
    )

    assert instructions == "Focus only on the selected task."


def test_apply_prompt_profile_changes_only_omnigent_instructions() -> None:
    original = AgentSpec(
        spec_version=1,
        name="omnigent",
        description="Base agent",
        instructions="Base instructions",
    )

    updated = apply_prompt_profile(original, "openai-agents", "Profile instructions")

    assert updated.instructions == "Profile instructions"
    assert updated.name == original.name
    assert updated.description == original.description
    assert apply_prompt_profile(original, "claude-sdk", "Ignored") is original


def test_non_profile_agents_are_not_prompt_profiles() -> None:
    assert is_prompt_profile(_profile(auto_select_enabled=None)) is False


def test_profile_list_filters_by_auto_select_flag() -> None:
    profile = _profile()
    ordinary_agent = _profile(id="ag_agent", name="ordinary", auto_select_enabled=None)
    disabled = _profile(id="ag_disabled", name="disabled", auto_select_enabled=False)

    class Store:
        def list(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(data=[profile, ordinary_agent, disabled], has_more=False)

    assert list_prompt_profiles(Store(), include_disabled=False) == [profile]  # type: ignore[arg-type]
    assert list_prompt_profiles(Store(), include_disabled=True) == [profile, disabled]  # type: ignore[arg-type]


async def test_auto_select_runs_again_for_each_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile()

    class Store:
        def list(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(data=[profile], has_more=False)

    class LLM:
        def __init__(self) -> None:
            self.inputs: list[str] = []

        async def create(self, **kwargs: object) -> SimpleNamespace:
            payload = kwargs["input"]  # type: ignore[index]
            text = payload[0]["content"][0]["text"]  # type: ignore[index]
            self.inputs.append(json.loads(text)["user_input"])
            return SimpleNamespace(
                output=[SimpleNamespace(content=[SimpleNamespace(text=profile.id)])]
            )

    llm = LLM()
    monkeypatch.setattr(
        "omnigent.profile_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.profile_selection.build_server_llm_client",
        lambda _config: llm,
    )

    await auto_select_prompt_profile("first turn", Store())  # type: ignore[arg-type]
    await auto_select_prompt_profile("second turn", Store())  # type: ignore[arg-type]

    assert llm.inputs == ["first turn", "second turn"]


@pytest.mark.parametrize(
    "profile",
    [
        None,
        _profile(enabled=False),
        _profile(archived=True),
        _profile(session_id="conv_session"),
    ],
)
def test_load_prompt_profile_rejects_unselectable_profiles(profile: Agent | None) -> None:
    with pytest.raises(OmnigentError, match="not found or unavailable"):
        load_prompt_profile_instructions(
            "ag_profile",
            _AgentStore(profile),  # type: ignore[arg-type]
            _AgentCache(),  # type: ignore[arg-type]
            require_selectable=True,
        )
