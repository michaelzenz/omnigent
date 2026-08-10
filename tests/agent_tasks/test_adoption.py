"""Unit tests for orphan session adoption."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.agent_tasks.adoption import (
    SESSION_ADOPTED,
    SESSION_ADOPTION_PROPOSAL,
    adopt_session,
    is_orphan_candidate,
    propose_session_adoption,
    reject_session_adoption,
)
from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_AGENT_NAME
from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.agent_tasks.role_keys import MANAGER_DEFAULT_ROLE_KEY
from omnigent.agent_tasks.session_labels import (
    ADOPTION_DISMISSED_LABEL,
    ROUTING_REPO_LABEL,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import TaskTag
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import OmnigentError
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
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

    task = task_store.create(_uid("task-bound"), "Bound task")
    worker_store.create_worker(
        _uid("worker-bound"),
        task.id,
        kind=WORKER_KIND_EXTERNAL,
        agent_profile_id=manager_agent_id,
        session_id=conv.id,
    )
    assert (
        is_orphan_candidate(
            conv,
            task_store=task_store,
            worker_store=worker_store,
        )
        is False
    )

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


def test_propose_session_adoption_requires_routing_tags(stores: _Stores) -> None:
    (
        conversation_store,
        task_store,
        task_event_store,
        worker_store,
        manager_agent_id,
    ) = stores
    conv = conversation_store.create_conversation(title="Needs tags", agent_id=manager_agent_id)
    with pytest.raises(OmnigentError):
        propose_session_adoption(
            session_id=conv.id,
            task_store=task_store,
            task_event_store=task_event_store,
            worker_store=worker_store,
            conversation_store=conversation_store,
        )


async def test_propose_and_adopt_session(stores: _Stores) -> None:
    (
        conversation_store,
        task_store,
        task_event_store,
        worker_store,
        manager_agent_id,
    ) = stores
    conv = conversation_store.create_conversation(
        title="Upload retries", agent_id=manager_agent_id
    )
    conversation_store.set_labels(conv.id, {ROUTING_REPO_LABEL: "omnigent-fork"})
    task_id = _uid("task-upload")
    task = task_store.create(
        task_id,
        "Upload retries",
        tags=[TaskTag(task_id=task_id, tag_type="repo", tag="omnigent-fork")],
    )
    proposal = propose_session_adoption(
        session_id=conv.id,
        task_store=task_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
    )
    assert proposal.event_type == SESSION_ADOPTION_PROPOSAL
    assert proposal.state == "received"
    assert proposal.source_key == conv.id

    params = resolve_bootstrap_params(
        host_id=_uid("host"),
        workspace="/tmp/test",
        harness="cursor",
        model="composer-2.5",
        role_profile=TaskRoleProfile(
            role=MANAGER_DEFAULT_ROLE_KEY,
            kind="manager",
            agent_profile_id=manager_agent_id,
            created_at=1,
        ),
    )
    async def _mock_session_creator(*, body: Any, request: Any, user_id: Any, **kwargs: Any):
        return conversation_store.create_conversation(
            title=body.title or "Task manager",
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
        )

    processed, adopted = await adopt_session(
        session_id=conv.id,
        task_id=task.id,
        task_store=task_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        params=params,
        proposal_event=proposal,
        session_creator=_mock_session_creator,
        app_state=SimpleNamespace(),
    )
    assert processed.state == "reconciled"
    assert adopted.event_type == SESSION_ADOPTED
    assert adopted.state == "routed"
    worker = worker_store.get_by_session_id(conv.id)
    assert worker is not None
    assert worker.kind == WORKER_KIND_EXTERNAL
    assert worker.task_id == task.id
    # An adopted session was never spawned from a role, so it carries the
    # session's own agent instead of a role key.
    assert worker.role_key is None
    assert worker.agent_profile_id == conv.agent_id


def test_reject_session_adoption_sets_dismiss_label(stores: _Stores) -> None:
    (
        conversation_store,
        task_store,
        task_event_store,
        worker_store,
        manager_agent_id,
    ) = stores
    conv = conversation_store.create_conversation(title="Stay orphan", agent_id=manager_agent_id)
    conversation_store.set_labels(conv.id, {ROUTING_REPO_LABEL: "misc-repo"})
    proposal = propose_session_adoption(
        session_id=conv.id,
        task_store=task_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
    )
    dismissed = reject_session_adoption(
        session_id=conv.id,
        conversation_store=conversation_store,
        task_event_store=task_event_store,
        proposal_event=proposal,
    )
    assert dismissed is not None
    assert dismissed.state == "dismissed"
    updated = conversation_store.get_conversation(conv.id)
    assert updated is not None
    assert updated.labels.get(ADOPTION_DISMISSED_LABEL) == "1"
    assert worker_store.get_by_session_id(conv.id) is None
