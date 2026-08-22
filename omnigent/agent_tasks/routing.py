"""Shared helpers for binding events to task managers."""

from __future__ import annotations

import uuid
from typing import Any

from omnigent.agent_tasks.bootstrap import BootstrapParams, bootstrap_task_manager
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

ROUTED_EVENT_STATE = "routed"


def record_routing_attempt(
    *,
    event_id: str,
    task_id: str,
    task_event_store: TaskEventStore,
    reason: str,
    score: float | None = None,
) -> str:
    """Persist one routing decision for monitoring."""
    attempt_id = uuid.uuid4().hex
    task_event_store.create_routing_attempt(
        attempt_id,
        event_id,
        task_id,
        score=score,
        reason=reason,
    )
    return attempt_id


async def route_event_to_task(
    *,
    event: TaskEvent,
    task: Task,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    params: BootstrapParams,
    routing_reason: str | None = None,
    routing_score: float | None = None,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
) -> TaskEvent:
    """Bootstrap the task manager when needed and bind the event for triage."""
    if routing_reason is not None:
        record_routing_attempt(
            event_id=event.id,
            task_id=task.id,
            task_event_store=task_event_store,
            reason=routing_reason,
            score=routing_score,
        )
    bootstrapped = await bootstrap_task_manager(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        params=params,
        session_creator=session_creator,
        app_state=app_state,
        user_id=user_id,
    )
    routed_at = now_epoch()
    updated = task_event_store.update_event(
        event.id,
        task_id=bootstrapped.id,
        state=ROUTED_EVENT_STATE,
        routed_at=routed_at,
    )
    if updated is None:
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    return updated
