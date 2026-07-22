"""Manager proposal fulfillment and user ack/edit flows."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from omnigent.agent_tasks.dispatch import (
    create_work_item_event,
    dispatch_worker_for_event,
    parse_dispatch_payload,
    resolve_dispatch_params,
)
from omnigent.agent_tasks.event_types import MANAGER_PROPOSAL
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskEventExecution
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore

ProposalResolution = Literal["accept_proposal", "edit_and_dispatch", "reject_proposal"]


def _merge_payload(
    base: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(base)
    if overrides:
        merged.update(overrides)
    return merged


def resolve_proposal(
    *,
    event: TaskEvent,
    resolution: ProposalResolution,
    task: Task,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    edited_payload: dict[str, Any] | None = None,
    secretary_profile: UserSecretaryProfile | None = None,
) -> tuple[TaskEvent, TaskEventExecution | None]:
    """Fulfill, edit, or reject a manager proposal event."""
    if event.event_type != MANAGER_PROPOSAL:
        raise OmnigentError("Not a manager proposal event", code=ErrorCode.INVALID_INPUT)
    if event.state != "awaiting_user_ack":
        raise OmnigentError(
            f"Cannot resolve proposal in state {event.state!r}",
            code=ErrorCode.CONFLICT,
        )
    if resolution == "reject_proposal":
        updated = task_event_store.update_event(event.id, state="dismissed")
        if updated is None:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        return updated, None

    payload = parse_dispatch_payload(event.payload)
    merged = _merge_payload(payload, edited_payload)
    params = resolve_dispatch_params(
        payload=merged,
        secretary_profile=secretary_profile,
    )
    work_item = create_work_item_event(
        task=task,
        task_event_store=task_event_store,
        title=params.title,
        payload=merged,
    )
    execution, _worker_id = dispatch_worker_for_event(
        task=task,
        event=work_item,
        params=params,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
    )
    task_event_store.update_event(
        event.id,
        state="processed",
        processed_at=now_epoch(),
    )
    updated = task_event_store.get_event(event.id)
    assert updated is not None
    return updated, execution


def create_manager_proposal(
    *,
    task: Task,
    task_event_store: TaskEventStore,
    title: str,
    payload: dict[str, Any],
    summary: str | None = None,
) -> TaskEvent:
    """Create a manager proposal awaiting user acknowledgement."""
    event_id = uuid.uuid4().hex
    return task_event_store.create_event(
        event_id,
        MANAGER_PROPOSAL,
        title,
        task_id=task.id,
        payload=json.dumps(payload),
        source="manager",
        summary=summary,
        state="awaiting_user_ack",
        manager_agent_id=task.manager_agent_id,
        manager_conversation_id=task.manager_conversation_id,
    )
