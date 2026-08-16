"""Tests for the default safety policy seed migration."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy.engine import Engine

from omnigent.db.utils import (
    _build_alembic_config,
    clear_engine_cache,
    get_or_create_engine,
)
from omnigent.stores.policy_store.sqlalchemy_store import SqlAlchemyPolicyStore

_PREVIOUS_HEAD = "g0a1b2c3d4e5"
_EXPECTED_POLICIES = {
    "deny_pii_in_llm_requests",
    "detect_tool_call_retry_loops",
    "detect_agent_thrashing",
    "dangerous_actions_intent_classifier",
}


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """Create a fresh SQLite database migrated to head."""
    uri = f"sqlite:///{tmp_path / 'test.db'}"
    engine = get_or_create_engine(uri)
    try:
        yield engine
    finally:
        clear_engine_cache()


def test_fresh_install_seeds_enabled_global_policies(db_engine: Engine) -> None:
    """A new installation starts with the complete safety baseline enabled."""
    policies = SqlAlchemyPolicyStore(str(db_engine.url)).list_defaults()

    assert {policy.name for policy in policies} == _EXPECTED_POLICIES
    assert all(policy.enabled for policy in policies)
    assert all(policy.scope == "default" for policy in policies)

    pii_policy = next(policy for policy in policies if policy.name == "deny_pii_in_llm_requests")
    assert pii_policy.factory_params == {"pii_types": ["secret", "API_KEY"]}


def test_upgrade_preserves_existing_policy_with_same_name(db_engine: Engine) -> None:
    """Seeding does not replace an operator-configured policy with a matching name."""
    uri = str(db_engine.url)
    config = _build_alembic_config(uri)
    with db_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, _PREVIOUS_HEAD)

    store = SqlAlchemyPolicyStore(uri)
    existing = store.create_default(
        policy_id="4f7259c18c2e4de69f9d19c629733c9a",
        name="detect_agent_thrashing",
        type="python",
        handler="omnigent.policies.builtins.context.detect_thrashing",
        enabled=False,
        created_by="operator",
    )

    with db_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")

    policies = store.list_defaults()
    assert {policy.name for policy in policies} == _EXPECTED_POLICIES
    preserved = next(policy for policy in policies if policy.name == "detect_agent_thrashing")
    assert preserved.id == existing.id
    assert preserved.enabled is False
    assert preserved.created_by == "operator"
