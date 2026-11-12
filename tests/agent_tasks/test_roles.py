"""Tests for the GlobalHub role catalog and each user's role definitions."""

from __future__ import annotations

import pytest

from omnigent.agent_tasks.roles import (
    BUILTIN_ROLES,
    CUSTODIAN_ROLE_KEY,
    MANAGER_ROLE_KEY,
    BuiltinRole,
    builtin_role,
    role_definition,
    session_role,
)
from omnigent.agent_tasks.session_labels import CUSTODIAN_ROLE_VALUE, ROLE_LABEL


@pytest.mark.parametrize("role", BUILTIN_ROLES, ids=lambda role: role.key)
def test_each_role_ships_a_self_contained_manual(role: BuiltinRole) -> None:
    manual = role.default_manual()

    assert manual.startswith(f"# {role.title} manual")
    # Hosts have no checkout of these docs, so manuals cannot point at one.
    assert "host.pmv2.root" not in manual


def test_session_role_reads_the_role_label() -> None:
    assert session_role({ROLE_LABEL: CUSTODIAN_ROLE_VALUE}) is builtin_role(CUSTODIAN_ROLE_KEY)
    assert session_role({ROLE_LABEL: "task_secretary"}) is None
    assert session_role({}) is None
    assert session_role(None) is None


def test_definition_applies_overrides_over_defaults() -> None:
    manager = builtin_role(MANAGER_ROLE_KEY)
    assert manager is not None

    default = role_definition(manager, {})
    custom = role_definition(
        manager, {"manual": "Route nothing.", "model": "example-model", "unknown": "ignored"}
    )

    assert (default.description, default.model, default.customized) == (
        manager.description,
        None,
        (),
    )
    assert default.manual == manager.default_manual()
    assert (custom.description, custom.model, custom.manual) == (
        manager.description,
        "example-model",
        "Route nothing.",
    )
    assert custom.customized == ("model", "manual")
