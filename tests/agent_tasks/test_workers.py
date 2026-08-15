"""Tests for worker lane helpers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.agent_tasks.bootstrap import bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.role_keys import MANAGER_DEFAULT_ROLE_KEY, WORKER_DEFAULT_ROLE_KEY
from omnigent.agent_tasks.workers import activate_worker_lane
from omnigent.db.utils import generate_agent_id
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.mark.asyncio
async def test_activate_worker_lane_starts_session(db_uri: str) -> None:
    task_store = SqlAlchemyTaskStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    agent_store = SqlAlchemyAgentStore(db_uri)

    manager_agent_id = generate_agent_id()
    worker_agent_id = generate_agent_id()
    agent_store.create(manager_agent_id, name="task-manager", bundle_location="test:///bundle")
    agent_store.create(worker_agent_id, name="coding-agent", bundle_location="test:///bundle")

    manager_profile = TaskRoleProfile(
        role=MANAGER_DEFAULT_ROLE_KEY,
        kind="manager",
        agent_profile_id=manager_agent_id,
        harness="cursor",
        model="composer-2.5",
        host_id=_uid("host"),
        workspace="/tmp/omnigent-worker-test",
        created_at=1,
    )
    worker_profile = TaskRoleProfile(
        role=WORKER_DEFAULT_ROLE_KEY,
        kind="worker",
        agent_profile_id=worker_agent_id,
        harness="cursor",
        model="composer-2.5",
        host_id=_uid("host"),
        workspace="/tmp/omnigent-worker-test",
        created_at=1,
    )

    task = task_store.create(
        _uid("task"),
        owner_user_id=_uid("owner"),
        title="Worker activate task",
        manager_role_key=MANAGER_DEFAULT_ROLE_KEY,
        worker_role_key=WORKER_DEFAULT_ROLE_KEY,
    )
    params = resolve_bootstrap_params(
        host_id=manager_profile.host_id,
        workspace=manager_profile.workspace,
        harness=manager_profile.harness,
        model=manager_profile.model,
        role_profile=manager_profile,
    )

    async def _mock_session_creator(*, body: Any, request: Any, user_id: Any, **kwargs: Any):
        return conversation_store.create_conversation(
            title=body.title or "Task manager",
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
            kind=(getattr(body, "parent_session_id", None) and "sub_agent") or "default",
            parent_conversation_id=getattr(body, "parent_session_id", None),
        )

    task = await bootstrap_task_manager(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        params=params,
        session_creator=_mock_session_creator,
        app_state=SimpleNamespace(),
    )

    worker = worker_store.create_worker(
        _uid("worker"),
        task.id,
        role_key=WORKER_DEFAULT_ROLE_KEY,
    )
    activated, conversation_id = await activate_worker_lane(
        task=task,
        worker=worker,
        task_store=task_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        manager_role_profile=manager_profile,
        worker_role_profile=worker_profile,
        session_creator=_mock_session_creator,
        app_state=SimpleNamespace(),
    )
    assert activated.session_id == conversation_id
    assert activated.agent_profile_id is None
    conv = conversation_store.get_conversation(conversation_id)
    assert conv is not None
    assert conv.kind == "sub_agent"
    assert conv.parent_conversation_id == task.manager_conversation_id
