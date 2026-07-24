"""Packaged built-in agents for managed task roles."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnigent.stores.agent_store import AgentStore

TASK_SECRETARY_AGENT_NAME = "task-secretary"
TASK_MANAGER_AGENT_NAME = "task-manager"
TASK_WORKER_AGENT_NAME = "task-worker"
TASK_REVIEWER_AGENT_NAME = "task-reviewer"
TASK_DOCS_AGENT_NAME = "task-docs"

TASK_BUILTIN_AGENT_NAMES: tuple[str, ...] = (
    TASK_SECRETARY_AGENT_NAME,
    TASK_MANAGER_AGENT_NAME,
    TASK_WORKER_AGENT_NAME,
    TASK_REVIEWER_AGENT_NAME,
    TASK_DOCS_AGENT_NAME,
)

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
