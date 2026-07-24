"""Tests for managed-task default constants."""

from __future__ import annotations

from omnigent.agent_tasks.constants import DEFAULT_TASK_HARNESS, resolve_task_harness


def test_default_task_harness_is_cursor_native() -> None:
    assert DEFAULT_TASK_HARNESS == "cursor-native"


def test_resolve_task_harness_maps_cursor_to_native() -> None:
    assert resolve_task_harness("cursor-native") == "cursor-native"
    assert resolve_task_harness("cursor") == "cursor-native"


def test_resolve_task_harness_passthrough_other_harnesses() -> None:
    assert resolve_task_harness("claude") == "claude"
