"""Tests for :class:`SqlAlchemyTaskRoleProfileStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_ROLE_DEFAULTS,
    TASK_SECRETARY_ROLE,
)
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
    secretary_defaults = TASK_ROLE_DEFAULTS[TASK_SECRETARY_ROLE]
    assert created.harness == secretary_defaults.harness
    assert created.model == secretary_defaults.model
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


def test_clear_model_unsets_a_stored_model(store: SqlAlchemyTaskRoleProfileStore) -> None:
    """Switching to a harness that owns its model clears the previous pick."""
    store.upsert(
        "alice@example.com",
        TASK_SECRETARY_ROLE,
        agent_profile_id="a" * 32,
        harness="cursor-native",
        model="composer-2.5",
    )
    # A bare None model means "leave unchanged", so the stale pick survives.
    unchanged = store.upsert("alice@example.com", TASK_SECRETARY_ROLE, harness="codex-native")
    assert unchanged.model == "composer-2.5"

    cleared = store.upsert(
        "alice@example.com",
        TASK_SECRETARY_ROLE,
        harness="codex-native",
        clear_model=True,
    )
    assert cleared.model is None
    assert store.get("alice@example.com", TASK_SECRETARY_ROLE).model is None


def test_upsert_and_get_broker_profile(store: SqlAlchemyTaskRoleProfileStore) -> None:
    created = store.upsert(
        "alice@example.com",
        TASK_BROKER_ROLE,
        agent_profile_id="b" * 32,
    )
    broker_defaults = TASK_ROLE_DEFAULTS[TASK_BROKER_ROLE]
    assert created.harness == broker_defaults.harness
    assert created.model == broker_defaults.model
    assert created.role == TASK_BROKER_ROLE
    loaded = store.get("alice@example.com", TASK_BROKER_ROLE)
    assert loaded is not None
    assert loaded.agent_profile_id == "b" * 32

    # Secretary and broker profiles coexist independently for the same user.
    store.upsert(
        "alice@example.com",
        TASK_SECRETARY_ROLE,
        agent_profile_id="a" * 32,
    )
    assert store.get("alice@example.com", TASK_SECRETARY_ROLE) is not None
    assert store.get("alice@example.com", TASK_BROKER_ROLE) is not None
    assert store.get("alice@example.com", TASK_SECRETARY_ROLE).agent_profile_id == "a" * 32
    assert store.get("alice@example.com", TASK_BROKER_ROLE).agent_profile_id == "b" * 32
