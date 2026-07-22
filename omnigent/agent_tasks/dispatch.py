"""Dispatch task workers as manager sub-agent sessions."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.bootstrap import BootstrapParams, resolve_bootstrap_params
from omnigent.agent_tasks.constants import DISPATCHABLE_EVENT_STATES
from omnigent.agent_tasks.event_types import MANAGER_WORK_ITEM
from omnigent.agent_tasks.executions import mark_execution_running, start_execution
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskEventExecution
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore


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


def _generate_work_item_id() -> str:
    return uuid.uuid4().hex


def parse_dispatch_payload(payload: str | None) -> dict[str, Any]:
    """Parse a JSON dispatch payload from a task event."""
    if payload is None or not payload.strip():
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OmnigentError("payload must be valid JSON", code=ErrorCode.INVALID_INPUT) from exc
    if not isinstance(parsed, dict):
        raise OmnigentError("payload must be a JSON object", code=ErrorCode.INVALID_INPUT)
    return parsed


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
    """Merge explicit dispatch fields with event payload and profile defaults."""
    resolved_worker = worker_agent_id or payload.get("worker_agent_id")
    resolved_title = title or payload.get("title")
    resolved_instructions = instructions or payload.get("instructions")
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


def create_work_item_event(
    *,
    task: Task,
    task_event_store: TaskEventStore,
    title: str,
    payload: dict[str, Any],
    source: str = "manager",
) -> TaskEvent:
    """Create an internal work-item event already bound to a task."""
    event_id = _generate_work_item_id()
    event = task_event_store.create_event(
        event_id,
        MANAGER_WORK_ITEM,
        title,
        task_id=task.id,
        payload=json.dumps(payload),
        source=source,
        state="routed",
        manager_agent_id=task.manager_agent_id,
        manager_conversation_id=task.manager_conversation_id,
    )
    updated = task_event_store.update_event(event.id, routed_at=now_epoch())
    return updated if updated is not None else event


def dispatch_worker_for_event(
    *,
    task: Task,
    event: TaskEvent,
    params: DispatchParams,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
) -> tuple[TaskEventExecution, str]:
    """
    Spawn a worker sub-agent session and record the execution.

    :returns: The execution row and worker conversation id.
    """
    if task.manager_conversation_id is None:
        raise OmnigentError(
            "Task manager is not bootstrapped",
            code=ErrorCode.CONFLICT,
        )
    if event.state not in DISPATCHABLE_EVENT_STATES:
        raise OmnigentError(
            f"Cannot dispatch for event in state {event.state!r}",
            code=ErrorCode.CONFLICT,
        )
    manager_conv = conversation_store.get_conversation(task.manager_conversation_id)
    if manager_conv is None:
        raise OmnigentError(
            "Manager session is missing",
            code=ErrorCode.CONFLICT,
        )
    if event.task_id is None:
        task_event_store.update_event(
            event.id,
            task_id=task.id,
            manager_agent_id=task.manager_agent_id,
            manager_conversation_id=task.manager_conversation_id,
            state="routed",
            routed_at=now_epoch(),
        )
        event = task_event_store.get_event(event.id)
        assert event is not None

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
    execution = start_execution(
        task=task,
        event=event,
        worker_agent_id=params.worker_agent_id,
        task_event_store=task_event_store,
        conversation_id=worker_conv.id,
        status="running",
    )
    mark_execution_running(
        task_event_store,
        execution.id,
        conversation_id=worker_conv.id,
    )
    task_event_store.upsert_binding(
        worker_conv.id,
        task.id,
        task.manager_agent_id,
        "worker",
        manager_conversation_id=task.manager_conversation_id,
    )
    refreshed = task_event_store.get_execution(execution.id)
    assert refreshed is not None
    return refreshed, worker_conv.id
