"""Load worker glossary roles for task dispatch."""

from __future__ import annotations

from omnigent.agent_tasks.broker_session import get_or_create_role_profile
from omnigent.entities import Task, Worker
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore


def worker_lane_role_key(task: Task, worker: Worker | None) -> str:
    """Return the role a lane runs, falling back to the task's lane default."""
    if worker is not None and worker.role_key:
        return worker.role_key
    return task.worker_role_key


def load_worker_role_profile(
    task_role_profile_store: TaskRoleProfileStore,
    task: Task,
    worker: Worker | None = None,
) -> TaskRoleProfile | None:
    """Return the role this worker lane runs."""
    return task_role_profile_store.get(worker_lane_role_key(task, worker))


def get_or_create_worker_role_profile(
    *,
    task_role_profile_store: TaskRoleProfileStore,
    host_store: HostStore,
    agent_store: AgentStore,
    auth_user_id: str | None,
    task: Task,
    worker: Worker | None = None,
) -> TaskRoleProfile | None:
    """Load or auto-provision the role for one worker lane."""
    existing = load_worker_role_profile(task_role_profile_store, task, worker)
    if existing is not None:
        return existing
    return get_or_create_role_profile(
        role=worker_lane_role_key(task, worker),
        auth_user_id=auth_user_id,
        task_role_profile_store=task_role_profile_store,
        host_store=host_store,
        agent_store=agent_store,
    )
