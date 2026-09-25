"""Defaults for pmv2 roles, all executed by OmniHarness."""

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
    """Non-prompt defaults used while creating a role binding.

    Engine comes from the bound execution-target bundle; roles pin only
    the model (bundles declare no executor.model) and a description.
    """

    model: str
    description: str | None = None


TASK_ROLE_DEFAULTS: dict[str, TaskRoleDefaults] = {
    TASK_BROKER_ROLE: TaskRoleDefaults(
        model="databricks-glm-5-3-flash",
        description="Triages incoming events and routes work to tasks.",
    ),
    TASK_SECRETARY_ROLE: TaskRoleDefaults(
        model="databricks-glm-5-3-flash",
        description="Helps the user steer GlobalHub.",
    ),
    MANAGER_DEFAULT_ROLE_KEY: TaskRoleDefaults(
        model="databricks-glm-5-3-flash",
        description="Owns a task, plans work, and supervises Workers.",
    ),
}


def task_role_defaults_for_key(role: str) -> TaskRoleDefaults | None:
    defaults = TASK_ROLE_DEFAULTS.get(role)
    if defaults is not None:
        return defaults
    fallback = resolve_template_defaults_role_key(role)
    return TASK_ROLE_DEFAULTS.get(fallback) if fallback is not None else None
