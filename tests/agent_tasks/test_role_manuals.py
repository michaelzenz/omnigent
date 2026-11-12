"""Tests for the instructions attached to each turn of a GlobalHub role session."""

from __future__ import annotations

from types import SimpleNamespace

from omnigent.agent_tasks.role_manuals import role_turn_instructions
from omnigent.agent_tasks.roles import MANAGER_ROLE_KEY, BuiltinRole, builtin_role
from omnigent.stores.user_settings_store import UserSettingsStore

ALICE = "alice@example.com"
BOB = "bob@example.com"


def _manager() -> BuiltinRole:
    role = builtin_role(MANAGER_ROLE_KEY)
    assert role is not None
    return role


def _conversation_store(db_uri: str, owner: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        storage_location=db_uri,
        get_session_owner=lambda _session_id, *, owner_only: owner,
    )


def test_turn_instructions_use_the_session_owners_manual(db_uri: str) -> None:
    settings = UserSettingsStore(db_uri)
    settings.save_globalhub_role_overrides(
        MANAGER_ROLE_KEY, {"manual": "Alice's manual."}, user_id=ALICE
    )
    settings.save_globalhub_role_overrides(
        MANAGER_ROLE_KEY, {"manual": "Local manual."}, user_id=None
    )

    def instructions(owner: str | None) -> str:
        return role_turn_instructions(
            _manager(), "conv_role", _conversation_store(db_uri, owner), None
        )

    assert instructions(ALICE) == "Alice's manual."
    # Single-user sessions have no owner grant and use the local user's settings.
    assert instructions(None) == "Local manual."
    assert instructions(BOB) == _manager().default_manual()


def test_turn_instructions_keep_request_instructions_after_the_manual(db_uri: str) -> None:
    conversation_store = _conversation_store(db_uri, ALICE)
    manual = _manager().default_manual()

    assert role_turn_instructions(_manager(), "conv_role", conversation_store, "  ") == manual
    assert (
        role_turn_instructions(_manager(), "conv_role", conversation_store, "Be brief.")
        == f"{manual}\n\nBe brief."
    )
    # Without SQL storage there are no saved overrides to read.
    assert role_turn_instructions(_manager(), "conv_role", object(), None) == manual
