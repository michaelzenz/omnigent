"""Load manager glossary roles for task bootstrap."""

from __future__ import annotations

from omnigent.agent_tasks.broker_session import get_or_create_role_profile
from omnigent.entities import Task
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.prompt_profile_store import PromptProfileStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore


def load_manager_role_profile(
    task_role_profile_store: TaskRoleProfileStore,
    task: Task,
) -> TaskRoleProfile | None:
    """Return the role selected by ``task.manager_role_key``."""
    return task_role_profile_store.get(task.manager_role_key)


def get_or_create_manager_role_profile(
    *,
    task_role_profile_store: TaskRoleProfileStore,
    host_store: HostStore,
    agent_store: AgentStore,
    auth_user_id: str | None,
    task: Task,
    prompt_profile_store: PromptProfileStore | None = None,
) -> TaskRoleProfile | None:
    """Load or auto-provision the manager role for ``task``."""
    existing = load_manager_role_profile(task_role_profile_store, task)
    if (
        existing is not None
        and (existing.prompt_profile_id or prompt_profile_store is None)
        and existing.host_id is not None
    ):
        return existing
    return get_or_create_role_profile(
        role=task.manager_role_key,
        auth_user_id=auth_user_id,
        task_role_profile_store=task_role_profile_store,
        host_store=host_store,
        agent_store=agent_store,
        prompt_profile_store=prompt_profile_store,
    )
