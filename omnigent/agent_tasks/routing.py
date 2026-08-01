"""Shared helpers for binding events to task managers."""

from __future__ import annotations

from omnigent.agent_tasks.bootstrap import BootstrapParams, bootstrap_task_manager
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

ROUTED_EVENT_STATE = "routed"


def route_event_to_task(
    *,
    event: TaskEvent,
    task: Task,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    params: BootstrapParams,
    selected_attempt_id: str | None = None,
) -> TaskEvent:
    """Bootstrap the task manager when needed and bind the event for triage."""
    bootstrapped = bootstrap_task_manager(
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        params=params,
    )
    routed_at = now_epoch()
    updated = task_event_store.update_event(
        event.id,
        task_id=bootstrapped.id,
        state=ROUTED_EVENT_STATE,
        selected_routing_attempt_id=selected_attempt_id,
        routed_at=routed_at,
    )
    if updated is None:
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    return updated
