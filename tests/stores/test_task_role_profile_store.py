"""Tests for :class:`SqlAlchemyTaskRoleProfileStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.agent_builtins import TASK_DISTRIBUTOR_ROLE, TASK_SECRETARY_ROLE
from omnigent.agent_tasks.constants import DEFAULT_SECRETARY_HARNESS, DEFAULT_SECRETARY_MODEL
from omnigent.stores.task_role_profile_store.sqlalchemy_store import SqlAlchemyTaskRoleProfileStore


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskRoleProfileStore:
    return SqlAlchemyTaskRoleProfileStore(db_uri)


def test_upsert_and_get_secretary_profile(store: SqlAlchemyTaskRoleProfileStore) -> None:
    created = store.upsert(
        "alice@example.com",
        TASK_SECRETARY_ROLE,
        agent_profile_id="a" * 32,
        host_id=uuid.uuid4().hex,
        workspace="/tmp/workspace",
    )
    assert created.harness == DEFAULT_SECRETARY_HARNESS
    assert created.model == DEFAULT_SECRETARY_MODEL
    assert created.role == TASK_SECRETARY_ROLE
    loaded = store.get("alice@example.com", TASK_SECRETARY_ROLE)
    assert loaded is not None
    assert loaded.agent_profile_id == "a" * 32
    assert loaded.workspace == "/tmp/workspace"

    updated = store.upsert(
        "alice@example.com",
        TASK_SECRETARY_ROLE,
        harness="cursor",
        model="composer-2.5",
        conversation_id="c" * 32,
    )
    assert updated.harness == "cursor"
    assert updated.conversation_id == "c" * 32


def test_upsert_and_get_distributor_profile(store: SqlAlchemyTaskRoleProfileStore) -> None:
    created = store.upsert(
        "alice@example.com",
        TASK_DISTRIBUTOR_ROLE,
        agent_profile_id="b" * 32,
    )
    assert created.harness == "cursor-native"
    assert created.model == "composer-2.5"
    assert created.role == TASK_DISTRIBUTOR_ROLE
    loaded = store.get("alice@example.com", TASK_DISTRIBUTOR_ROLE)
    assert loaded is not None
    assert loaded.agent_profile_id == "b" * 32

    # Secretary and distributor profiles coexist independently for the same user.
    store.upsert(
        "alice@example.com",
        TASK_SECRETARY_ROLE,
        agent_profile_id="a" * 32,
    )
    assert store.get("alice@example.com", TASK_SECRETARY_ROLE) is not None
    assert store.get("alice@example.com", TASK_DISTRIBUTOR_ROLE) is not None
    assert (
        store.get("alice@example.com", TASK_SECRETARY_ROLE).agent_profile_id == "a" * 32
    )
    assert (
        store.get("alice@example.com", TASK_DISTRIBUTOR_ROLE).agent_profile_id == "b" * 32
    )
