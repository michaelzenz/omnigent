"""Tests for the packaged GlobalHub role manuals."""

from __future__ import annotations

import pytest

from omnigent.agent_tasks.role_manuals import default_role_manual, role_turn_instructions
from omnigent.agent_tasks.session_labels import (
    BROKER_ROLE_VALUE,
    CUSTODIAN_ROLE_VALUE,
    MANAGER_ROLE_VALUE,
    ROLE_LABEL,
)


@pytest.mark.parametrize(
    ("role", "heading"),
    [
        (BROKER_ROLE_VALUE, "# Task broker manual"),
        (MANAGER_ROLE_VALUE, "# Task manager manual"),
        (CUSTODIAN_ROLE_VALUE, "# Task custodian manual"),
    ],
)
def test_each_role_ships_a_self_contained_manual(role: str, heading: str) -> None:
    manual = default_role_manual(role)

    assert manual is not None
    assert manual.startswith(heading)
    # Hosts have no checkout of these docs, so manuals cannot point at one.
    assert "host.pmv2.root" not in manual


def test_unknown_role_has_no_manual() -> None:
    assert default_role_manual("task_secretary") is None


def test_turn_instructions_put_the_manual_before_request_instructions() -> None:
    manager = {ROLE_LABEL: MANAGER_ROLE_VALUE}
    manual = default_role_manual(MANAGER_ROLE_VALUE)

    assert role_turn_instructions(manager, None) == manual
    assert role_turn_instructions(manager, "  ") == manual
    assert role_turn_instructions(manager, "Be brief.") == f"{manual}\n\nBe brief."
    assert role_turn_instructions({}, "Be brief.") is None
    assert role_turn_instructions({ROLE_LABEL: "task_secretary"}, None) is None
