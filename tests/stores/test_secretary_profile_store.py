"""Tests for :class:`SqlAlchemySecretaryProfileStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.constants import DEFAULT_SECRETARY_HARNESS, DEFAULT_SECRETARY_MODEL
from omnigent.stores.secretary_profile_store.sqlalchemy_store import SqlAlchemySecretaryProfileStore


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemySecretaryProfileStore:
    return SqlAlchemySecretaryProfileStore(db_uri)


def test_upsert_and_get_profile(store: SqlAlchemySecretaryProfileStore) -> None:
    created = store.upsert(
        "alice@example.com",
        agent_id="a" * 32,
        host_id=uuid.uuid4().hex,
        workspace="/tmp/workspace",
    )
    assert created.harness == DEFAULT_SECRETARY_HARNESS
    assert created.model == DEFAULT_SECRETARY_MODEL
    loaded = store.get("alice@example.com")
    assert loaded is not None
    assert loaded.agent_id == "a" * 32
    assert loaded.workspace == "/tmp/workspace"

    updated = store.upsert(
        "alice@example.com",
        harness="cursor",
        model="composer-2.5",
        conversation_id="c" * 32,
    )
    assert updated.harness == "cursor"
    assert updated.conversation_id == "c" * 32
