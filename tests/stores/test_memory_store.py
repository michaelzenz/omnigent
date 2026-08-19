from __future__ import annotations

import uuid

import pytest

from omnigent.errors import OmnigentError
from omnigent.stores.memory_store import DEFAULT_MEMORY_CATEGORY_NAMES
from omnigent.stores.memory_store.sqlalchemy_store import SqlAlchemyMemoryStore


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyMemoryStore:
    return SqlAlchemyMemoryStore(db_uri)


def test_list_seeds_defaults_once(store: SqlAlchemyMemoryStore) -> None:
    first = store.list(user_id="alice")
    second = store.list(user_id="alice")
    assert [category.name for category in first] == list(DEFAULT_MEMORY_CATEGORY_NAMES)
    assert [category.id for category in second] == [category.id for category in first]
    assert all(category.content == "" and category.token_count == 0 for category in first)


def test_crud_is_owner_scoped(store: SqlAlchemyMemoryStore) -> None:
    created = store.create(
        uuid.uuid4().hex,
        user_id="alice",
        name="Custom",
        content="remember this",
    )
    assert created.token_count > 0
    assert store.update(created.id, user_id="bob", content="stolen") is None
    updated = store.update(created.id, user_id="alice", content="changed")
    assert updated is not None and updated.content == "changed"
    assert not store.delete(created.id, user_id="bob")
    assert store.delete(created.id, user_id="alice")


def test_reorder_requires_and_applies_complete_order(store: SqlAlchemyMemoryStore) -> None:
    categories = store.list(user_id=None)
    reversed_ids = [category.id for category in reversed(categories)]
    reordered = store.reorder(reversed_ids, user_id=None)
    assert [category.id for category in reordered] == reversed_ids
    assert [category.display_order for category in reordered] == list(range(len(categories)))
    with pytest.raises(OmnigentError):
        store.reorder(reversed_ids[:-1], user_id=None)


def test_memory_limit_is_owner_scoped(store: SqlAlchemyMemoryStore) -> None:
    assert store.get_max_tokens(user_id="alice", default=20_000) == 20_000
    assert store.set_max_tokens(12_000, user_id="alice") == 12_000
    assert store.get_max_tokens(user_id="alice", default=20_000) == 12_000
    assert store.get_max_tokens(user_id="bob", default=20_000) == 20_000
