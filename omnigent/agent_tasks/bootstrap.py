"""Bootstrap a live manager session for a managed task."""

from __future__ import annotations

from dataclasses import dataclass

from omnigent.agent_tasks.agent_builtins import (
    TASK_MANAGER_AGENT_NAME,
    resolve_task_agent_id,
)
from omnigent.agent_tasks.constants import (
    DEFAULT_TASK_HARNESS,
    DEFAULT_TASK_MODEL,
    DEFAULT_TASK_WORKSPACE,
    resolve_task_harness,
)
from omnigent.entities import Task
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore


@dataclass(frozen=True)
class BootstrapParams:
    """Resolved host/workspace/harness/model inputs for manager bootstrap."""

    host_id: str
    workspace: str
    harness: str
    model: str


def resolve_bootstrap_params(
    *,
    host_id: str | None,
    workspace: str | None,
    harness: str | None,
    model: str | None,
    secretary_profile: UserSecretaryProfile | None,
) -> BootstrapParams:
    """Merge explicit bootstrap inputs with secretary profile defaults."""
    resolved_host_id = host_id or (secretary_profile.host_id if secretary_profile else None)
    resolved_workspace = (
        workspace
        or (secretary_profile.workspace if secretary_profile else None)
        or DEFAULT_TASK_WORKSPACE
    )
    # Host/workspace come from the secretary profile; harness/model use task-agent defaults.
    resolved_harness = resolve_task_harness(harness or DEFAULT_TASK_HARNESS)
    resolved_model = model or DEFAULT_TASK_MODEL
    if not resolved_host_id or not resolved_workspace:
        raise OmnigentError(
            "host_id and workspace are required to bootstrap a manager session",
            code=ErrorCode.INVALID_INPUT,
        )
    return BootstrapParams(
        host_id=resolved_host_id,
        workspace=resolved_workspace,
        harness=resolved_harness,
        model=resolved_model,
    )


def bootstrap_task_manager(
    *,
    task: Task,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    params: BootstrapParams,
) -> Task:
    """
    Ensure ``task`` has a live manager conversation.

    Idempotent when ``manager_conversation_id`` points at an existing conversation.
    Returns ``CONFLICT`` when the stored id is set but the conversation is gone.
    """
    if task.manager_conversation_id is not None:
        existing = conversation_store.get_conversation(task.manager_conversation_id)
        if existing is None:
            raise OmnigentError(
                "Manager session is missing; clear manager_conversation_id before re-bootstrap",
                code=ErrorCode.CONFLICT,
            )
        return task

    manager_agent_id = resolve_task_agent_id(
        agent_store,
        TASK_MANAGER_AGENT_NAME,
        fallback_agent_id=task.manager_agent_id,
    )

    conversation = conversation_store.create_conversation(
        title=f"Task manager: {task.title}",
        agent_id=manager_agent_id,
        host_id=params.host_id,
        workspace=params.workspace,
    )
    conversation_store.update_conversation(
        conversation.id,
        harness_override=params.harness,
        model_override=params.model,
    )
    updated = task_store.update(
        task.id,
        manager_conversation_id=conversation.id,
    )
    if updated is None:
        raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
    task_event_store.upsert_binding(
        conversation.id,
        task.id,
        manager_agent_id,
        "manager",
        manager_conversation_id=conversation.id,
    )
    return updated
