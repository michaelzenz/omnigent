"""Tests for glossary role profile key validation."""

from __future__ import annotations

import pytest

from omnigent.agent_tasks.role_keys import (
    MANAGER_DEFAULT_ROLE_KEY,
    ROLE_KIND_BROKER,
    ROLE_KIND_EXTERNAL,
    ROLE_KIND_MANAGER,
    ROLE_KIND_SECRETARY,
    ROLE_KIND_WORKER,
    TASK_BROKER_ROLE_KEY,
    TASK_SECRETARY_ROLE_KEY,
    WORKER_DEFAULT_ROLE_KEY,
    is_deletable_role_key,
    is_singleton_role_key,
    is_system_role_key,
    manager_role_key_from_slug,
    normalize_role_profile_key,
    role_kind_from_key,
    role_profile_title,
    worker_role_key_from_slug,
)
from omnigent.errors import OmnigentError


def test_system_roles_are_not_deletable() -> None:
    assert is_system_role_key("broker")
    assert is_system_role_key(MANAGER_DEFAULT_ROLE_KEY)
    assert is_system_role_key(WORKER_DEFAULT_ROLE_KEY)
    assert not is_deletable_role_key(MANAGER_DEFAULT_ROLE_KEY)
    assert not is_deletable_role_key(WORKER_DEFAULT_ROLE_KEY)


def test_custom_manager_role_is_deletable() -> None:
    role = manager_role_key_from_slug("research")
    assert role == "manager:research"
    assert is_deletable_role_key(role)
    assert role_profile_title(role) == "Task manager (research)"


def test_custom_worker_role_is_deletable() -> None:
    role = worker_role_key_from_slug("research")
    assert role == "worker:research"
    assert is_deletable_role_key(role)
    assert role_profile_title(role) == "Task worker (research)"


def test_reserved_template_slug_rejected() -> None:
    with pytest.raises(OmnigentError):
        manager_role_key_from_slug("default")
    with pytest.raises(OmnigentError):
        worker_role_key_from_slug("default")


def test_normalize_accepts_template_roles() -> None:
    assert normalize_role_profile_key("manager:default") == MANAGER_DEFAULT_ROLE_KEY
    assert normalize_role_profile_key("worker:default") == WORKER_DEFAULT_ROLE_KEY


def test_role_kind_from_key_covers_every_family() -> None:
    assert role_kind_from_key(TASK_BROKER_ROLE_KEY) == ROLE_KIND_BROKER
    assert role_kind_from_key(TASK_SECRETARY_ROLE_KEY) == ROLE_KIND_SECRETARY
    assert role_kind_from_key(MANAGER_DEFAULT_ROLE_KEY) == ROLE_KIND_MANAGER
    assert role_kind_from_key(WORKER_DEFAULT_ROLE_KEY) == ROLE_KIND_WORKER
    # A key outside the glossary belongs to no template family.
    assert role_kind_from_key("adopted-session") == ROLE_KIND_EXTERNAL


def test_only_user_facing_roles_are_singletons() -> None:
    """Broker and secretary sessions are per-user; task roles are per task/lane."""
    assert is_singleton_role_key(TASK_BROKER_ROLE_KEY)
    assert is_singleton_role_key(TASK_SECRETARY_ROLE_KEY)
    assert not is_singleton_role_key(MANAGER_DEFAULT_ROLE_KEY)
    assert not is_singleton_role_key(WORKER_DEFAULT_ROLE_KEY)
