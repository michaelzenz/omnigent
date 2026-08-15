"""Packaged built-in agents for managed task roles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from omnigent.agent_tasks.role_keys import (
    MANAGER_DEFAULT_ROLE_KEY,
    WORKER_DEFAULT_ROLE_KEY,
    is_manager_role_key,
    is_worker_role_key,
    resolve_template_defaults_role_key,
)
from omnigent.stores.agent_store import AgentStore

TASK_BROKER_AGENT_NAME = "task-broker"
TASK_BROKER_ROLE = "broker"
TASK_SECRETARY_AGENT_NAME = "task-secretary"
TASK_SECRETARY_ROLE = "secretary"
TASK_MANAGER_AGENT_NAME = "task-manager"
TASK_MANAGER_ROLE = "manager"
TASK_WORKER_AGENT_NAME = "coding-agent"
DEFAULT_WORKER_AGENT_NAME = "default-worker"

PER_USER_TASK_ROLES: frozenset[str] = frozenset({TASK_BROKER_ROLE, TASK_SECRETARY_ROLE})

TASK_BUILTIN_AGENT_NAMES: tuple[str, ...] = (
    TASK_BROKER_AGENT_NAME,
    TASK_SECRETARY_AGENT_NAME,
    TASK_MANAGER_AGENT_NAME,
    TASK_WORKER_AGENT_NAME,
    DEFAULT_WORKER_AGENT_NAME,
)


@dataclass(frozen=True)
class TaskRoleDefaults:
    """Built-in agent and runtime defaults for one task role."""

    agent_name: str
    harness: str
    model: str


TASK_ROLE_DEFAULTS: dict[str, TaskRoleDefaults] = {
    TASK_BROKER_ROLE: TaskRoleDefaults(
        agent_name=TASK_BROKER_AGENT_NAME,
        harness="openai-agents",
        model="databricks-glm-5-2",
    ),
    TASK_SECRETARY_ROLE: TaskRoleDefaults(
        agent_name=TASK_SECRETARY_AGENT_NAME,
        harness="openai-agents",
        model="databricks-glm-5-2",
    ),
    MANAGER_DEFAULT_ROLE_KEY: TaskRoleDefaults(
        agent_name=TASK_MANAGER_AGENT_NAME,
        harness="openai-agents",
        model="databricks-glm-5-2",
    ),
    WORKER_DEFAULT_ROLE_KEY: TaskRoleDefaults(
        agent_name=DEFAULT_WORKER_AGENT_NAME,
        harness="openai-agents",
        model="databricks-glm-5-2",
    ),
}


def task_role_defaults_for_key(role: str) -> TaskRoleDefaults | None:
    """Return packaged defaults for a glossary role key."""
    defaults = TASK_ROLE_DEFAULTS.get(role)
    if defaults is not None:
        return defaults
    fallback_key = resolve_template_defaults_role_key(role)
    if fallback_key is None:
        return None
    return TASK_ROLE_DEFAULTS.get(fallback_key)


_AGENTS_DIR = Path(__file__).parent / "agents"


def task_agent_spec_path(agent_name: str) -> Path:
    """Return the packaged YAML spec for a task-role built-in agent."""
    return _AGENTS_DIR / f"{agent_name}.yaml"


def resolve_task_agent_id(
    agent_store: AgentStore,
    agent_name: str,
    *,
    fallback_agent_id: str | None = None,
) -> str:
    """Return the registered built-in id for *agent_name*, or *fallback_agent_id*."""
    row = agent_store.get_by_name(agent_name)
    if row is not None:
        return row.id
    if fallback_agent_id is not None:
        return fallback_agent_id
    raise ValueError(f"built-in task agent {agent_name!r} is not registered")


def resolve_role_agent_profile_id(
    agent_store: AgentStore,
    role: str,
    *,
    fallback_agent_id: str | None = None,
) -> str:
    """Return the built-in agent profile id for *role*, or *fallback_agent_id*."""
    defaults = TASK_ROLE_DEFAULTS.get(role)
    if defaults is None and is_manager_role_key(role):
        return resolve_task_agent_id(
            agent_store,
            TASK_MANAGER_AGENT_NAME,
            fallback_agent_id=fallback_agent_id,
        )
    if defaults is None and is_worker_role_key(role):
        return resolve_task_agent_id(
            agent_store,
            DEFAULT_WORKER_AGENT_NAME,
            fallback_agent_id=fallback_agent_id,
        )
    if defaults is None:
        if fallback_agent_id is not None:
            return fallback_agent_id
        raise ValueError(f"no built-in agent defaults for task role {role!r}")
    return resolve_task_agent_id(
        agent_store,
        defaults.agent_name,
        fallback_agent_id=fallback_agent_id,
    )
