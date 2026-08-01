"""Dispatch task workers against TaskItem backlog units."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.agent_tasks.executions import mark_execution_running, start_execution_for_item
from omnigent.entities import Task, TaskEventExecution, TaskItem
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore


@dataclass(frozen=True)
class DispatchParams:
    """Resolved worker dispatch inputs."""

    worker_agent_id: str
    title: str
    instructions: str
    host_id: str
    workspace: str
    harness: str
    model: str


def parse_dispatch_payload(payload: str | None) -> dict[str, Any]:
    """Parse a JSON dispatch payload."""
    if payload is None or not payload.strip():
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OmnigentError("payload must be valid JSON", code=ErrorCode.INVALID_INPUT) from exc
    if not isinstance(parsed, dict):
        raise OmnigentError("payload must be a JSON object", code=ErrorCode.INVALID_INPUT)
    return parsed


def compose_worker_instructions(
    *,
    instructions: str | None,
    internal_note: str | None,
) -> str:
    """Merge worker instructions with agent-facing context."""
    worker_text = (instructions or "").strip()
    note = (internal_note or "").strip()
    if note and worker_text:
        return f"{worker_text}\n\n## Context\n{note}"
    if note:
        return note
    return worker_text


def resolve_dispatch_params(
    *,
    payload: dict[str, Any],
    worker_agent_id: str | None = None,
    title: str | None = None,
    instructions: str | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    secretary_profile: UserSecretaryProfile | None = None,
) -> DispatchParams:
    """Merge explicit dispatch fields with payload and profile defaults."""
    resolved_worker = worker_agent_id or payload.get("worker_agent_id")
    resolved_title = title or payload.get("title")
    resolved_instructions = compose_worker_instructions(
        instructions=instructions if instructions is not None else payload.get("instructions"),
        internal_note=payload.get("internal_note"),
    )
    if not resolved_worker or not resolved_title or not resolved_instructions:
        raise OmnigentError(
            "worker_agent_id, title, and instructions are required",
            code=ErrorCode.INVALID_INPUT,
        )
    bootstrap = resolve_bootstrap_params(
        host_id=host_id or payload.get("host_id"),
        workspace=workspace or payload.get("workspace"),
        harness=harness or payload.get("harness"),
        model=model or payload.get("model"),
        secretary_profile=secretary_profile,
    )
    return DispatchParams(
        worker_agent_id=str(resolved_worker),
        title=str(resolved_title),
        instructions=str(resolved_instructions),
        host_id=bootstrap.host_id,
        workspace=bootstrap.workspace,
        harness=bootstrap.harness,
        model=bootstrap.model,
    )


def dispatch_worker_for_item(
    *,
    task: Task,
    item: TaskItem,
    params: DispatchParams,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
) -> tuple[TaskEventExecution, str]:
    """Spawn a worker sub-agent session for one task item."""
    if task.manager_conversation_id is None:
        raise OmnigentError(
            "Task manager is not bootstrapped",
            code=ErrorCode.CONFLICT,
        )
    if item.task_id != task.id:
        raise OmnigentError("Task item does not belong to task", code=ErrorCode.INVALID_INPUT)
    manager_conv = conversation_store.get_conversation(task.manager_conversation_id)
    if manager_conv is None:
        raise OmnigentError(
            "Manager session is missing",
            code=ErrorCode.CONFLICT,
        )

    if manager_conv.agent_id is None:
        raise OmnigentError(
            "Manager session has no agent binding",
            code=ErrorCode.CONFLICT,
        )
    manager_agent_id = manager_conv.agent_id

    linked_events = task_item_store.list_events_for_item(item.id)
    trigger_event_id = linked_events[0].event_id if linked_events else None

    worker_conv = conversation_store.create_conversation(
        kind="sub_agent",
        title=params.title,
        parent_conversation_id=task.manager_conversation_id,
        agent_id=params.worker_agent_id,
        runner_id=manager_conv.runner_id,
        host_id=params.host_id,
        workspace=params.workspace,
    )
    conversation_store.update_conversation(
        worker_conv.id,
        harness_override=params.harness,
        model_override=params.model,
    )
    execution = start_execution_for_item(
        task=task,
        item=item,
        manager_agent_id=manager_agent_id,
        worker_agent_id=params.worker_agent_id,
        task_event_store=task_event_store,
        event_id=trigger_event_id,
        conversation_id=worker_conv.id,
        status="running",
    )
    mark_execution_running(
        task_event_store,
        execution.id,
        conversation_id=worker_conv.id,
    )
    task_item_store.update_item(item.id, state="running")
    task_event_store.upsert_binding(
        worker_conv.id,
        task.id,
        manager_agent_id,
        "worker",
        manager_conversation_id=task.manager_conversation_id,
    )
    refreshed = task_event_store.get_execution(execution.id)
    assert refreshed is not None
    return refreshed, worker_conv.id
