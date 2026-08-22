"""Focused tests for PuppyGarden Worker Provider configuration."""

from __future__ import annotations

import json

from omnigent.db.utils import generate_agent_id
from omnigent.server.routes.worker_providers import (
    _internal_configuration,
    ensure_default_worker_provider,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.worker_provider_store.sqlalchemy_store import (
    SqlAlchemyWorkerProviderStore,
)


def test_internal_configuration_keeps_only_execution_target_and_model() -> None:
    assert _internal_configuration(
        {
            "agent_id": "ag_worker",
            "model": "model-a",
            "harness": "stale-hidden-override",
            "host_id": "stale-host",
            "workspace": "/stale/workspace",
        }
    ) == {
        "agent_id": "ag_worker",
        "model": "model-a",
    }


def test_fresh_default_provider_uses_omniharness(db_uri: str) -> None:
    agent_store = SqlAlchemyAgentStore(db_uri)
    provider_store = SqlAlchemyWorkerProviderStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="omniharness", bundle_location="test:///omniharness")

    ensure_default_worker_provider(provider_store, agent_store)

    [provider] = provider_store.list()
    assert provider.name == "Default Worker"
    assert json.loads(provider.configuration) == {"agent_id": agent_id}
