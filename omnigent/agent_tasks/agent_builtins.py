"""Defaults for PuppyGarden roles, all executed by OmniHarness."""

from __future__ import annotations

from dataclasses import dataclass

from omnigent.agent_tasks.role_keys import (
    MANAGER_DEFAULT_ROLE_KEY,
    TASK_BROKER_ROLE_KEY,
    TASK_SECRETARY_ROLE_KEY,
    resolve_template_defaults_role_key,
)

TASK_BROKER_ROLE = TASK_BROKER_ROLE_KEY
TASK_SECRETARY_ROLE = TASK_SECRETARY_ROLE_KEY
TASK_MANAGER_ROLE = "manager"


@dataclass(frozen=True)
class TaskRoleDefaults:
    """Non-prompt defaults used while creating a role binding."""

    harness: str
    model: str
    description: str | None = None


TASK_ROLE_DEFAULTS: dict[str, TaskRoleDefaults] = {
    TASK_BROKER_ROLE: TaskRoleDefaults(
        harness="openai-agents",
        model="databricks-glm-5-2",
        description="Triages incoming events and routes work to tasks.",
    ),
    TASK_SECRETARY_ROLE: TaskRoleDefaults(
        harness="openai-agents",
        model="databricks-glm-5-2",
        description="Helps the user steer PuppyGarden.",
    ),
    MANAGER_DEFAULT_ROLE_KEY: TaskRoleDefaults(
        harness="openai-agents",
        model="databricks-glm-5-2",
        description="Owns a task, plans work, and supervises Workers.",
    ),
}


def task_role_defaults_for_key(role: str) -> TaskRoleDefaults | None:
    defaults = TASK_ROLE_DEFAULTS.get(role)
    if defaults is not None:
        return defaults
    fallback = resolve_template_defaults_role_key(role)
    return TASK_ROLE_DEFAULTS.get(fallback) if fallback is not None else None
