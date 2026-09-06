"""Tests for the durable manager store."""

from __future__ import annotations

import uuid

import pytest

from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def store(db_uri: str) -> SqlAlchemyManagerStore:
    return SqlAlchemyManagerStore(db_uri)


def test_manager_crud_and_owner_isolated_list(store: SqlAlchemyManagerStore) -> None:
    mine_id = _uid("manager-mine")
    theirs_id = _uid("manager-theirs")

    created = store.upsert(
        mine_id,
        owner_user_id="user-1",
        role_key="manager:default",
        description="Owns upload reliability.",
        conversation_id=_uid("session-mine"),
    )
    store.upsert(
        theirs_id,
        owner_user_id="user-2",
        role_key="manager:review",
        description="Owns review automation.",
        conversation_id=_uid("session-theirs"),
    )

    assert created.id == mine_id
    assert created.conversation_id == _uid("session-mine")
    assert store.get(mine_id) == created
    assert [manager.id for manager in store.list(owner_user_id="user-1")] == [mine_id]
    assert [manager.id for manager in store.list(owner_user_id="user-2")] == [theirs_id]
    assert store.list(owner_user_id="__anonymous__") == []

    updated = store.update(
        mine_id,
        role_key="manager:uploads",
        description="Owns all upload operations.",
    )
    assert updated is not None
    assert updated.role_key == "manager:uploads"
    assert updated.description == "Owns all upload operations."
    # Session pointer survives a metadata-only update.
    assert updated.conversation_id == created.conversation_id

    reassigned = store.update(mine_id, owner_user_id="user-2")
    assert reassigned is not None
    assert reassigned.owner_user_id == "user-2"
    assert store.list(owner_user_id="user-1") == []
    assert {manager.id for manager in store.list(owner_user_id="user-2")} == {
        mine_id,
        theirs_id,
    }
    assert store.update(_uid("missing-manager"), description="missing") is None
    assert store.delete(_uid("missing-manager")) is False


def test_upsert_updates_existing_manager_without_duplicating(
    store: SqlAlchemyManagerStore,
) -> None:
    manager_id = _uid("manager-upsert")
    created = store.upsert(
        manager_id,
        owner_user_id="user-1",
        role_key="manager:default",
        description="Initial scope.",
        conversation_id=_uid("session-a"),
    )
    updated = store.upsert(
        manager_id,
        owner_user_id="user-1",
        role_key="manager:review",
        description="Updated scope.",
        conversation_id=_uid("session-b"),
    )

    assert updated.id == manager_id
    assert updated.created_at == created.created_at
    assert updated.role_key == "manager:review"
    assert updated.description == "Updated scope."
    # Upsert re-points the session pointer (the heal path's contract).
    assert updated.conversation_id == _uid("session-b")
    assert store.list(owner_user_id="user-1") == [updated]


def test_update_repoints_session_pointer(store: SqlAlchemyManagerStore) -> None:
    """The heal path swaps the session pointer without touching metadata."""
    manager_id = _uid("manager-heal")
    original_session = _uid("session-dead")
    store.upsert(
        manager_id,
        owner_user_id="user-1",
        role_key="manager:default",
        description="Heal me.",
        conversation_id=original_session,
    )

    healed = store.update(manager_id, conversation_id=_uid("session-fresh"))
    assert healed is not None
    assert healed.conversation_id == _uid("session-fresh")
    assert healed.description == "Heal me."
    assert healed.role_key == "manager:default"

    # The reverse lookup finds the manager by either session binding.
    assert store.get_by_conversation_id(_uid("session-fresh")).id == manager_id
    assert store.get_by_conversation_id(_uid("session-never")) is None


def test_delete_removes_manager_row(store: SqlAlchemyManagerStore) -> None:
    manager_id = _uid("manager-delete")
    store.upsert(
        manager_id,
        owner_user_id="user-1",
        role_key="manager:default",
        description="Doomed.",
        conversation_id=_uid("session-doomed"),
    )
    assert store.get(manager_id) is not None
    assert store.delete(manager_id) is True
    assert store.get(manager_id) is None
    assert store.delete(manager_id) is False


def test_null_owner_normalizes_to_anonymous(store: SqlAlchemyManagerStore) -> None:
    manager = store.upsert(
        _uid("anonymous-manager"),
        owner_user_id=None,
        role_key="manager:default",
        description="Local manager.",
        conversation_id=_uid("session-anon"),
    )

    assert manager.owner_user_id == "__anonymous__"
