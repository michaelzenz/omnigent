"""Tests for managed-task default constants."""

from __future__ import annotations

import pytest

from omnigent.agent_tasks.constants import DEFAULT_TASK_HARNESS, resolve_task_harness
from omnigent.errors import OmnigentError


def test_default_task_harness_is_headless_cursor() -> None:
    assert DEFAULT_TASK_HARNESS == "cursor"


def test_resolve_task_harness_raises_without_cursor_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "omnigent.onboarding.cursor_auth.cursor_sdk_installed",
        lambda: False,
    )
    with pytest.raises(OmnigentError) as exc_info:
        resolve_task_harness("cursor")
    assert "cursor-sdk" in str(exc_info.value)
    assert resolve_task_harness("cursor-native") == "cursor-native"


def test_resolve_task_harness_keeps_cursor_when_sdk_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "omnigent.onboarding.cursor_auth.cursor_sdk_installed",
        lambda: True,
    )
    assert resolve_task_harness("cursor") == "cursor"
