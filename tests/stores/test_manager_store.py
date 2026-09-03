"""Tests for :class:`SqlAlchemyManagerStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture()
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
    )
    store.upsert(
        theirs_id,
        owner_user_id="user-2",
        role_key="manager:review",
        description="Owns review automation.",
    )

    assert created.conversation_id == mine_id
    assert store.get(mine_id) == created
    assert [manager.conversation_id for manager in store.list(owner_user_id="user-1")] == [
        mine_id
    ]
    assert [manager.conversation_id for manager in store.list(owner_user_id="user-2")] == [
        theirs_id
    ]
    assert store.list(owner_user_id="__anonymous__") == []

    updated = store.update(
        mine_id,
        role_key="manager:uploads",
        description="Owns all upload operations.",
    )
    assert updated is not None
    assert updated.role_key == "manager:uploads"
    assert updated.description == "Owns all upload operations."

    reassigned = store.update(mine_id, owner_user_id="user-2")
    assert reassigned is not None
    assert reassigned.owner_user_id == "user-2"
    assert store.list(owner_user_id="user-1") == []
    assert {manager.conversation_id for manager in store.list(owner_user_id="user-2")} == {
        mine_id,
        theirs_id,
    }
    assert store.update(_uid("missing-manager"), description="missing") is None


def test_upsert_updates_existing_manager_without_duplicating(
    store: SqlAlchemyManagerStore,
) -> None:
    conversation_id = _uid("manager-upsert")
    created = store.upsert(
        conversation_id,
        owner_user_id="user-1",
        role_key="manager:default",
        description="Initial scope.",
    )
    updated = store.upsert(
        conversation_id,
        owner_user_id="user-1",
        role_key="manager:review",
        description="Updated scope.",
    )

    assert updated.conversation_id == created.conversation_id
    assert updated.created_at == created.created_at
    assert updated.role_key == "manager:review"
    assert updated.description == "Updated scope."
    assert store.list(owner_user_id="user-1") == [updated]


def test_null_owner_normalizes_to_anonymous(store: SqlAlchemyManagerStore) -> None:
    manager = store.upsert(
        _uid("anonymous-manager"),
        owner_user_id=None,
        role_key="manager:default",
        description="Local manager.",
    )

    assert manager.owner_user_id == "__anonymous__"
    assert store.list(owner_user_id=None) == [manager]
