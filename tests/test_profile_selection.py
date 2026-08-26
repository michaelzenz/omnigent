import json
from types import SimpleNamespace

import pytest

from omnigent.entities import PromptProfile
from omnigent.errors import OmnigentError
from omnigent.omniharness_turn_selection import select_omniharness_turn
from omnigent.profile_selection import load_prompt_profile_instructions
from omnigent.usage_ledger import canonical_purpose


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
        return [profile for profile in self.profiles if not enabled_only or profile.enabled]


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


@pytest.mark.parametrize("profile", [None])
def test_existing_fixed_selection_rejects_missing(
    profile: PromptProfile | None,
) -> None:
    with pytest.raises(OmnigentError, match="not found or unavailable"):
        load_prompt_profile_instructions(
            "11" * 16,
            _Store([] if profile is None else [profile]),  # type: ignore[arg-type]
            require_selectable=False,
        )


async def test_profile_only_auto_select_runs_again_for_each_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()

    class LLM:
        def __init__(self) -> None:
            self.inputs: list[list[str]] = []
            self.schemas: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> SimpleNamespace:
            payload = kwargs["input"]  # type: ignore[index]
            text = payload[0]["content"][0]["text"]  # type: ignore[index]
            self.inputs.append(json.loads(text)["user_messages"])
            self.schemas.append(kwargs["text"])  # type: ignore[arg-type]
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        content=[SimpleNamespace(text=json.dumps({"profile_id": profile.id}))]
                    )
                ]
            )

    llm = LLM()
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.build_server_llm_client",
        lambda _config: llm,
    )

    store = _Store([profile])
    await select_omniharness_turn(["first turn"], store)  # type: ignore[arg-type]
    await select_omniharness_turn(["first turn", "second turn"], store)  # type: ignore[arg-type]

    assert llm.inputs == [["first turn"], ["first turn", "second turn"]]
    assert all(
        "model" not in schema["format"]["schema"]["properties"]  # type: ignore[index]
        for schema in llm.schemas
    )


async def test_auto_select_accepts_dashed_profile_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(id="0123456789abcdef0123456789abcdef")

    class LLM:
        async def create(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        content=[
                            SimpleNamespace(
                                text=json.dumps(
                                    {"profile_id": "01234567-89ab-cdef-0123-456789abcdef"}
                                )
                            )
                        ]
                    )
                ]
            )

    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.build_server_llm_client",
        lambda _config: LLM(),
    )

    selection = await select_omniharness_turn(
        ["hello"],
        _Store([profile]),  # type: ignore[arg-type]
    )

    assert selection.profile == profile


async def test_auto_include_selects_multiple_suitable_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research = _profile(id="22" * 16, name="research")
    review = _profile(id="33" * 16, name="review")

    class LLM:
        async def create(self, **kwargs: object) -> SimpleNamespace:
            schema = kwargs["text"]["format"]["schema"]  # type: ignore[index]
            assert schema["properties"]["profile_ids"]["type"] == "array"
            assert "up to 5" in kwargs["instructions"]  # type: ignore[operator]
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        content=[
                            SimpleNamespace(
                                text=json.dumps({"profile_ids": [review.id, research.id]})
                            )
                        ]
                    )
                ]
            )

    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.build_server_llm_client",
        lambda _config: LLM(),
    )

    selection = await select_omniharness_turn(
        ["research and review this"],
        _Store([research, review]),  # type: ignore[arg-type]
        profile_mode="include",
        max_profiles=5,
    )

    assert selection.profiles == (review, research)
    assert selection.profile == review


async def test_auto_include_may_select_no_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    class LLM:
        async def create(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                output=[SimpleNamespace(content=[SimpleNamespace(text='{"profile_ids":[]}')])]
            )

    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.build_server_llm_client",
        lambda _config: LLM(),
    )

    selection = await select_omniharness_turn(
        ["hello"],
        _Store([_profile()]),  # type: ignore[arg-type]
        profile_mode="include",
    )

    assert selection.profiles == ()
    assert selection.profile is None


async def test_joint_auto_select_uses_one_call_for_profile_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()

    class LLM:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        content=[
                            SimpleNamespace(
                                text=json.dumps(
                                    {
                                        "profile_id": profile.id,
                                        "model": "model-powerful",
                                        "rationale": "Complex refactor.",
                                    }
                                )
                            )
                        ]
                    )
                ]
            )

    llm = LLM()
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.build_server_llm_client",
        lambda _config: llm,
    )

    selection = await select_omniharness_turn(
        ["refactor this package"],
        _Store([profile]),  # type: ignore[arg-type]
        model_candidates=["model-fast", "model-powerful"],
        decision_model="luna",
        smart_routing_prompt="Prefer accuracy for refactors.",
    )

    assert len(llm.calls) == 1
    assert selection.profile == profile
    assert selection.model == "model-powerful"
    assert selection.model_verdict == {
        "model": "model-powerful",
        "rationale": "Complex refactor.",
        "router_source": "oss-llm",
    }
    assert llm.calls[0]["model"] == "luna"
    request = json.loads(llm.calls[0]["input"][0]["content"][0]["text"])  # type: ignore[index]
    assert request["model_candidates"] == ["model-fast", "model-powerful"]
    assert request["routing_guidance"] == "Prefer accuracy for refactors."
    assert request["user_messages"] == ["refactor this package"]


async def test_joint_auto_select_rejects_unknown_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LLM:
        async def create(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        content=[
                            SimpleNamespace(
                                text=json.dumps(
                                    {
                                        "profile_id": "unknown",
                                        "model": "model-fast",
                                        "rationale": "Simple.",
                                    }
                                )
                            )
                        ]
                    )
                ]
            )

    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.build_server_llm_client",
        lambda _config: LLM(),
    )

    with pytest.raises(OmnigentError, match="unknown profile"):
        await select_omniharness_turn(
            ["hello"],
            _Store([_profile()]),  # type: ignore[arg-type]
            model_candidates=["model-fast"],
        )


async def test_workload_only_selection_uses_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LLM:
        def __init__(self) -> None:
            self.calls = 0
            self.kwargs: dict[str, object] = {}

        async def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            self.kwargs = kwargs
            return SimpleNamespace(
                model="judge",
                usage=SimpleNamespace(input_tokens=12, output_tokens=3),
                output=[
                    SimpleNamespace(
                        content=[SimpleNamespace(text=json.dumps({"workload": "research"}))]
                    )
                ],
            )

    llm = LLM()
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.get_caps",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "omnigent.omniharness_turn_selection.build_server_llm_client",
        lambda _config: llm,
    )

    selection = await select_omniharness_turn(
        ["diagnose this failure"],
        select_profile=False,
        classify_workload=True,
        workload_categories=["debug", "research", "other"],
        decision_model="judge",
    )

    assert llm.calls == 1
    assert selection.profile is None
    assert selection.model is None
    assert selection.workload == "research"
    assert json.loads(str(llm.kwargs["input"][0]["content"][0]["text"]))[
        "workload_categories"
    ] == ["debug", "research", "other"]
    assert selection.usage == {
        "input_tokens": 12,
        "output_tokens": 3,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def test_combined_purpose_is_canonical() -> None:
    assert (
        canonical_purpose(["workload_classification", "smart_routing", "profile_selection"])
        == "profile_selection+smart_routing+workload_classification"
    )
