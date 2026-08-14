"""Tests for :class:`SqlAlchemyTaskRoleProfileStore` and its session bindings."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_ROLE_DEFAULTS,
    TASK_SECRETARY_ROLE,
)
from omnigent.stores.task_role_profile_store.sqlalchemy_store import SqlAlchemyTaskRoleProfileStore
from omnigent.stores.user_role_session_store.sqlalchemy_store import SqlAlchemyUserRoleSessionStore


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskRoleProfileStore:
    return SqlAlchemyTaskRoleProfileStore(db_uri)


@pytest.fixture()
def session_store(db_uri: str) -> SqlAlchemyUserRoleSessionStore:
    return SqlAlchemyUserRoleSessionStore(db_uri)


def test_upsert_and_get_secretary_profile(store: SqlAlchemyTaskRoleProfileStore) -> None:
    created = store.upsert(
        TASK_SECRETARY_ROLE,
        agent_profile_id="a" * 32,
        host_id=uuid.uuid4().hex,
        workspace="/tmp/workspace",
    )
    secretary_defaults = TASK_ROLE_DEFAULTS[TASK_SECRETARY_ROLE]
    assert created.harness == secretary_defaults.harness
    assert created.model == secretary_defaults.model
    assert created.role == TASK_SECRETARY_ROLE
    assert created.kind == "secretary"
    loaded = store.get(TASK_SECRETARY_ROLE)
    assert loaded is not None
    assert loaded.agent_profile_id == "a" * 32
    assert loaded.workspace == "/tmp/workspace"

    updated = store.upsert(
        TASK_SECRETARY_ROLE,
        harness="cursor",
        model="composer-2.5",
    )
    assert updated.harness == "cursor"
    assert updated.agent_profile_id == "a" * 32


def test_clear_model_unsets_a_stored_model(store: SqlAlchemyTaskRoleProfileStore) -> None:
    """Switching to a harness that owns its model clears the previous pick."""
    store.upsert(
        TASK_SECRETARY_ROLE,
        agent_profile_id="a" * 32,
        harness="cursor-native",
        model="composer-2.5",
    )
    # A bare None model means "leave unchanged", so the stale pick survives.
    unchanged = store.upsert(TASK_SECRETARY_ROLE, harness="codex-native")
    assert unchanged.model == "composer-2.5"

    cleared = store.upsert(
        TASK_SECRETARY_ROLE,
        harness="codex-native",
        clear_model=True,
    )
    assert cleared.model is None
    assert store.get(TASK_SECRETARY_ROLE).model is None


def test_upsert_and_get_broker_profile(store: SqlAlchemyTaskRoleProfileStore) -> None:
    created = store.upsert(
        TASK_BROKER_ROLE,
        agent_profile_id="b" * 32,
    )
    broker_defaults = TASK_ROLE_DEFAULTS[TASK_BROKER_ROLE]
    assert created.harness == broker_defaults.harness
    assert created.model == broker_defaults.model
    assert created.role == TASK_BROKER_ROLE
    loaded = store.get(TASK_BROKER_ROLE)
    assert loaded is not None
    assert loaded.agent_profile_id == "b" * 32

    # Secretary and broker definitions coexist independently.
    store.upsert(
        TASK_SECRETARY_ROLE,
        agent_profile_id="a" * 32,
    )
    assert store.get(TASK_SECRETARY_ROLE) is not None
    assert store.get(TASK_BROKER_ROLE) is not None
    assert store.get(TASK_SECRETARY_ROLE).agent_profile_id == "a" * 32
    assert store.get(TASK_BROKER_ROLE).agent_profile_id == "b" * 32


def test_list_roles_filters_by_kind(store: SqlAlchemyTaskRoleProfileStore) -> None:
    """Role kinds partition the glossary so callers can list one family."""
    store.upsert(TASK_BROKER_ROLE, agent_profile_id="b" * 32)
    store.upsert("worker:reviewer", agent_profile_id="c" * 32)
    store.upsert("worker:builder", agent_profile_id="d" * 32)

    workers = store.list_roles(kind="worker")
    assert [profile.role for profile in workers] == ["worker:builder", "worker:reviewer"]
    assert {profile.role for profile in store.list_roles()} == {
        TASK_BROKER_ROLE,
        "worker:builder",
        "worker:reviewer",
    }


def test_delete_removes_a_role(store: SqlAlchemyTaskRoleProfileStore) -> None:
    store.upsert("worker:reviewer", agent_profile_id="c" * 32)
    assert store.delete("worker:reviewer") is True
    assert store.get("worker:reviewer") is None
    assert store.delete("worker:reviewer") is False


def test_role_session_is_per_user(session_store: SqlAlchemyUserRoleSessionStore) -> None:
    """A singleton role's live conversation is bound per user, not per role."""
    alice = session_store.set_conversation("alice@example.com", TASK_SECRETARY_ROLE, "c" * 32)
    assert alice.conversation_id == "c" * 32
    session_store.set_conversation("bob@example.com", TASK_SECRETARY_ROLE, "d" * 32)
    assert session_store.get("alice@example.com", TASK_SECRETARY_ROLE).conversation_id == "c" * 32
    assert session_store.get("bob@example.com", TASK_SECRETARY_ROLE).conversation_id == "d" * 32

    session_store.set_conversation("alice@example.com", TASK_BROKER_ROLE, "e" * 32)
    assert [row.role for row in session_store.list_for_user("alice@example.com")] == [
        TASK_BROKER_ROLE,
        TASK_SECRETARY_ROLE,
    ]

    # Clearing the binding keeps the row so the role stays known to the user.
    cleared = session_store.set_conversation("alice@example.com", TASK_SECRETARY_ROLE, None)
    assert cleared.conversation_id is None
    assert session_store.delete("alice@example.com", TASK_SECRETARY_ROLE) is True
    assert session_store.get("alice@example.com", TASK_SECRETARY_ROLE) is None
