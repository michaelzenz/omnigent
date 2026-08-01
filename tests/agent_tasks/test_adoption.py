"""Unit tests for orphan session adoption."""

from __future__ import annotations

import uuid

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
from omnigent.agent_tasks.session_labels import (
    ADOPTION_DISMISSED_LABEL,
    ROUTING_REPO_LABEL,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import TaskTag
from omnigent.errors import OmnigentError
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store import WORKER_KIND_EXTERNAL
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture()
def stores(
    db_uri: str,
) -> tuple[
    SqlAlchemyConversationStore,
    SqlAlchemyTaskStore,
    SqlAlchemyTaskEventStore,
    SqlAlchemyWorkerStore,
    SqlAlchemyAgentStore,
    str,
]:
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
        agent_store,
        manager_agent_id,
    )


def test_is_orphan_candidate_filters_bound_and_dismissed(
    stores: tuple[
        SqlAlchemyConversationStore,
        SqlAlchemyTaskStore,
        SqlAlchemyTaskEventStore,
        SqlAlchemyWorkerStore,
        SqlAlchemyAgentStore,
        str,
    ],
) -> None:
    (
        conversation_store,
        task_store,
        task_event_store,
        worker_store,
        agent_store,
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

    task = task_store.create(_uid("task-bound"), "Bound task", agent_profile_id=manager_agent_id)
    worker_store.create_worker(
        _uid("worker-bound"),
        task.id,
        manager_agent_id,
        kind=WORKER_KIND_EXTERNAL,
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


def test_propose_session_adoption_requires_routing_tags(
    stores: tuple[
        SqlAlchemyConversationStore,
        SqlAlchemyTaskStore,
        SqlAlchemyTaskEventStore,
        SqlAlchemyWorkerStore,
        SqlAlchemyAgentStore,
        str,
    ],
) -> None:
    (
        conversation_store,
        task_store,
        task_event_store,
        worker_store,
        agent_store,
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
            agent_store=agent_store,
        )


async def test_propose_and_adopt_session(
    stores: tuple[
        SqlAlchemyConversationStore,
        SqlAlchemyTaskStore,
        SqlAlchemyTaskEventStore,
        SqlAlchemyWorkerStore,
        SqlAlchemyAgentStore,
        str,
    ],
) -> None:
    (
        conversation_store,
        task_store,
        task_event_store,
        worker_store,
        agent_store,
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
        agent_profile_id=manager_agent_id,
        tags=[TaskTag(task_id=task_id, tag_type="repo", tag="omnigent-fork")],
    )
    proposal = propose_session_adoption(
        session_id=conv.id,
        task_store=task_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
    )
    assert proposal.event_type == SESSION_ADOPTION_PROPOSAL
    assert proposal.state == "received"
    assert proposal.source_key == conv.id

    params = resolve_bootstrap_params(
        host_id=_uid("host"),
        workspace="/tmp/test",
        harness="cursor",
        model="composer-2.5",
        role_profile=None,
    )
    processed, adopted = await adopt_session(
        session_id=conv.id,
        task_id=task.id,
        task_store=task_store,
        task_event_store=task_event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        params=params,
        proposal_event=proposal,
    )
    assert processed.state == "reconciled"
    assert adopted.event_type == SESSION_ADOPTED
    assert adopted.state == "routed"
    worker = worker_store.get_by_session_id(conv.id)
    assert worker is not None
    assert worker.kind == WORKER_KIND_EXTERNAL
    assert worker.task_id == task.id


def test_reject_session_adoption_sets_dismiss_label(
    stores: tuple[
        SqlAlchemyConversationStore,
        SqlAlchemyTaskStore,
        SqlAlchemyTaskEventStore,
        SqlAlchemyWorkerStore,
        SqlAlchemyAgentStore,
        str,
    ],
) -> None:
    (
        conversation_store,
        task_store,
        task_event_store,
        worker_store,
        agent_store,
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
        agent_store=agent_store,
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
