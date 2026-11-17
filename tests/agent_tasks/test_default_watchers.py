"""Tests for the watchers that a new GlobalHub user starts with."""

from __future__ import annotations

import pytest

from omnigent.agent_tasks.default_watchers import DEFAULT_WATCHERS, DefaultWatcher
from omnigent.agent_tasks.role_schedules import require_role_schedule
from omnigent.agent_tasks.roles import WATCHER_ROLE


@pytest.mark.parametrize("default", DEFAULT_WATCHERS, ids=lambda default: default.name)
def test_a_default_watcher_runs_the_template_with_its_own_watch_section(
    default: DefaultWatcher,
) -> None:
    template_head = WATCHER_ROLE.default_manual().partition("## What to watch")[0]

    watch_section = default.manual().removeprefix(template_head)

    assert watch_section.startswith("## What to watch\n\n")
    assert "Replace this example" not in watch_section
    assert "Check the items that you already track" in watch_section
    assert (
        watch_section.rstrip().rpartition("\n\n")[2].startswith("If you do not have access to the")
    )
    require_role_schedule(default.rrule, "UTC")
