"""Unit tests for orphan session adoption."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omnigent.agent_tasks.adoption import (
    is_orphan_candidate,
)
from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_AGENT_NAME
from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store import WORKER_KIND_EXTERNAL
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


_Stores = tuple[
    SqlAlchemyConversationStore,
    SqlAlchemyTaskStore,
    SqlAlchemyTaskEventStore,
    SqlAlchemyWorkerStore,
    str,
]


@pytest.fixture()
def stores(db_uri: str) -> _Stores:
    agent_store = SqlAlchemyAgentStore(db_uri)
    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id,
        name=TASK_MANAGER_AGENT_NAME,
        bundle_location="test:///bundle",
    )
    return (
        SqlAlchemyConversationStore(db_uri),
        SqlAlchemyTaskStore(db_uri),
        SqlAlchemyTaskEventStore(db_uri),
        SqlAlchemyWorkerStore(db_uri),
        manager_agent_id,
    )


def test_is_orphan_candidate_filters_bound_and_dismissed(stores: _Stores) -> None:
    (
        conversation_store,
        task_store,
        _task_event_store,
        worker_store,
        manager_agent_id,
    ) = stores
    conv = conversation_store.create_conversation(title="Orphan", agent_id=manager_agent_id)
    assert (
        is_orphan_candidate(
            conv,
            task_store=task_store,
            worker_store=worker_store,
        )
        is True
    )

    task = task_store.create(_uid("task-bound"), "Bound task", "bound goal")
    worker_store.create_worker(
        _uid("worker-bound"),
        task.id,
        kind=WORKER_KIND_EXTERNAL,
        target_id=conv.id,
    )
    assert (
        is_orphan_candidate(
            conv,
            task_store=task_store,
            worker_store=worker_store,
        )
        is False
    )

    from omnigent.agent_tasks.session_labels import ADOPTION_DISMISSED_LABEL

    orphan = conversation_store.create_conversation(title="Dismissed", agent_id=manager_agent_id)
    conversation_store.set_labels(orphan.id, {ADOPTION_DISMISSED_LABEL: "1"})
    dismissed = conversation_store.get_conversation(orphan.id)
    assert (
        is_orphan_candidate(
            dismissed,
            task_store=task_store,
            worker_store=worker_store,
        )
        is False
    )


def test_is_orphan_candidate_filters_sub_agents(stores: _Stores) -> None:
    (
        conversation_store,
        _task_store,
        _task_event_store,
        _worker_store,
        manager_agent_id,
    ) = stores
    parent = conversation_store.create_conversation(title="Parent", agent_id=manager_agent_id)
    child = conversation_store.create_conversation(
        kind="sub_agent", title="Child", agent_id=manager_agent_id,
        parent_conversation_id=parent.id,
    )
    assert (
        is_orphan_candidate(
            child,
            task_store=_task_store,
            worker_store=_worker_store,
        )
        is False
    )
