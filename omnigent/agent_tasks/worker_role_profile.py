"""Load worker glossary profiles for task dispatch."""

from __future__ import annotations

from omnigent.agent_tasks.broker_session import get_or_create_role_profile
from omnigent.entities import Task
from omnigent.entities.task_role_profile import UserTaskRoleProfile
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore


def load_worker_role_profile(
    task_role_profile_store: TaskRoleProfileStore,
    owner_user_id: str,
    task: Task,
) -> UserTaskRoleProfile | None:
    """Return the glossary profile selected by ``task.worker_role_key``."""
    return task_role_profile_store.get(owner_user_id, task.worker_role_key)


def get_or_create_worker_role_profile(
    *,
    task_role_profile_store: TaskRoleProfileStore,
    host_store: HostStore,
    agent_store: AgentStore,
    owner_user_id: str,
    auth_user_id: str | None,
    task: Task,
) -> UserTaskRoleProfile | None:
    """Load or auto-provision the worker glossary profile for ``task``."""
    existing = load_worker_role_profile(task_role_profile_store, owner_user_id, task)
    if existing is not None:
        return existing
    return get_or_create_role_profile(
        role=task.worker_role_key,
        profile_user_id=owner_user_id,
        auth_user_id=auth_user_id,
        task_role_profile_store=task_role_profile_store,
        host_store=host_store,
        agent_store=agent_store,
    )
