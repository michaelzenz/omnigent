"""Tests for managed-task default constants."""

from __future__ import annotations

from omnigent.agent_tasks.constants import (
    DEFAULT_SECRETARY_HARNESS,
    DEFAULT_TASK_HARNESS,
    resolve_task_harness,
)


def test_default_task_harness_is_cursor_native() -> None:
    assert DEFAULT_TASK_HARNESS == "cursor-native"


def test_default_secretary_harness_is_claude_native() -> None:
    assert DEFAULT_SECRETARY_HARNESS == "claude-native"


def test_resolve_task_harness_maps_headless_cursor_to_native() -> None:
    assert resolve_task_harness("cursor") == "cursor-native"
    assert resolve_task_harness("cursor-native") == "cursor-native"


def test_resolve_task_harness_maps_claude_to_native() -> None:
    assert resolve_task_harness("claude") == "claude-native"
    assert resolve_task_harness("claude-native") == "claude-native"
    assert resolve_task_harness("claude-sdk") == "claude-sdk"
