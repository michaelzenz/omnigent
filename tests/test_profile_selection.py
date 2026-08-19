import json
from types import SimpleNamespace

import pytest

from omnigent.entities import PromptProfile
from omnigent.errors import OmnigentError
from omnigent.profile_selection import auto_select_prompt_profile, load_prompt_profile_instructions


def _profile(**overrides: object) -> PromptProfile:
    values: dict[str, object] = {
        "id": "11" * 16,
        "created_at": 1,
        "name": "focused",
        "instructions": "Focus only on the selected task.",
    }
    values.update(overrides)
    return PromptProfile(**values)  # type: ignore[arg-type]


class _Store:
    def __init__(self, profiles: list[PromptProfile]) -> None:
        self.profiles = profiles

    def get(self, profile_id: str) -> PromptProfile | None:
        return next((profile for profile in self.profiles if profile.id == profile_id), None)

    def list(self, *, enabled_only: bool = False) -> list[PromptProfile]:
        return [
            profile
            for profile in self.profiles
            if not profile.archived and (not enabled_only or profile.enabled)
        ]


def test_fixed_new_selection_requires_enabled_profile() -> None:
    profile = _profile(enabled=False)
    with pytest.raises(OmnigentError, match="not found or unavailable"):
        load_prompt_profile_instructions(
            profile.id,
            _Store([profile]),  # type: ignore[arg-type]
            require_selectable=True,
        )


def test_existing_fixed_selection_may_read_disabled_profile() -> None:
    profile = _profile(enabled=False)
    assert (
        load_prompt_profile_instructions(
            profile.id,
            _Store([profile]),  # type: ignore[arg-type]
            require_selectable=False,
        )
        == profile.instructions
    )


@pytest.mark.parametrize("profile", [None, _profile(archived=True)])
def test_existing_fixed_selection_rejects_missing_or_archived(
    profile: PromptProfile | None,
) -> None:
    with pytest.raises(OmnigentError, match="not found or unavailable"):
        load_prompt_profile_instructions(
            "11" * 16,
            _Store([] if profile is None else [profile]),  # type: ignore[arg-type]
            require_selectable=False,
        )


async def test_auto_select_runs_again_for_each_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile()

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

    store = _Store([profile])
    await auto_select_prompt_profile("first turn", store)  # type: ignore[arg-type]
    await auto_select_prompt_profile("second turn", store)  # type: ignore[arg-type]

    assert llm.inputs == ["first turn", "second turn"]
