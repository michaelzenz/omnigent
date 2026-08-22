from __future__ import annotations

import pytest

from omnigent.errors import OmnigentError
from omnigent.stores.prompt_profile_store.sqlalchemy_store import SqlAlchemyPromptProfileStore


def test_prompt_profile_crud_and_active_filters(db_uri: str) -> None:
    store = SqlAlchemyPromptProfileStore(db_uri)
    enabled = store.create("11" * 16, "Enabled", "Enabled instructions")
    disabled = store.create(
        "22" * 16,
        "Disabled",
        "Disabled instructions",
        enabled=False,
    )

    assert store.list(enabled_only=True) == [enabled]
    assert [profile.id for profile in store.list()] == [enabled.id, disabled.id]

    updated = store.update(
        disabled.id,
        description="Now active",
        instructions="Updated",
        enabled=True,
    )
    assert updated is not None
    assert updated.description == "Now active"
    assert updated.instructions == "Updated"
    assert {profile.id for profile in store.list(enabled_only=True)} == {
        enabled.id,
        disabled.id,
    }

    deleted = store.delete(enabled.id)
    assert deleted is True
    assert store.list() == [updated]


def test_prompt_profile_name_can_be_reused_after_delete(db_uri: str) -> None:
    store = SqlAlchemyPromptProfileStore(db_uri)
    first = store.create("33" * 16, "Focused", "First")

    with pytest.raises(OmnigentError, match="name already exists"):
        store.create("44" * 16, "Focused", "Second")

    assert store.delete(first.id) is True
    second = store.create("44" * 16, "Focused", "Second")
    assert second is not None
