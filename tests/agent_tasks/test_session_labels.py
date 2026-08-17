"""Tests for PuppyGarden role-session label helpers."""

from __future__ import annotations

from omnigent.agent_tasks.session_labels import presentation_labels_for_harness


def test_sdk_harness_has_no_presentation_labels() -> None:
    """The in-process SDK harness is not a native wrapper, so role sessions on
    it get no ``omnigent.wrapper`` label — the PuppyGarden dock surfaces its own
    model switcher for them instead of the composer's native picker."""
    assert presentation_labels_for_harness("openai-agents") == {}


def test_native_harness_carries_wrapper_label() -> None:
    labels = presentation_labels_for_harness("claude-native")
    assert labels.get("omnigent.wrapper") == "claude-code-native-ui"
    assert labels.get("omnigent.ui") == "terminal"


def test_role_harness_alias_resolves_to_native() -> None:
    """A role profile may store the short alias (``claude``/``cursor``); the
    helper canonicalizes it so the wrapper label still lands on the session."""
    assert presentation_labels_for_harness("claude") == presentation_labels_for_harness(
        "claude-native"
    )
    assert presentation_labels_for_harness("cursor") == presentation_labels_for_harness(
        "cursor-native"
    )


def test_none_or_unknown_harness_is_empty() -> None:
    assert presentation_labels_for_harness(None) == {}
    assert presentation_labels_for_harness("") == {}
    assert presentation_labels_for_harness("not-a-real-harness") == {}
