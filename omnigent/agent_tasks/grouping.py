"""Secretary grouping proposals for orphan task events."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from omnigent.agent_tasks.bootstrap import bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.items import create_task_item
from omnigent.db.utils import now_epoch
from omnigent.entities import GroupingProposal, Task
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore

GroupingResolution = Literal["accept_grouping", "reject_grouping"]


def _generate_proposal_id() -> str:
    return uuid.uuid4().hex


def _generate_task_id() -> str:
    return uuid.uuid4().hex


def create_grouping_proposal(
    *,
    owner_user_id: str,
    payload: dict[str, Any],
    event_ids: list[str],
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
) -> GroupingProposal:
    """Create a secretary grouping proposal over orphan events."""
    proposal_id = _generate_proposal_id()
    for event_id in event_ids:
        event = task_event_store.get_event(event_id)
        if event is None:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        if event.state not in {"awaiting_grouping", "grouping_proposed"}:
            raise OmnigentError(
                f"Event {event_id} is not awaiting grouping",
                code=ErrorCode.CONFLICT,
            )
    proposal = task_item_store.create_grouping_proposal(
        proposal_id,
        owner_user_id,
        json.dumps(payload),
    )
    for event_id in event_ids:
        task_item_store.link_proposal_event(proposal_id, event_id)
        task_event_store.update_event(event_id, state="grouping_proposed")
    return proposal


def resolve_grouping_proposal(
    *,
    proposal: GroupingProposal,
    resolution: GroupingResolution,
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    secretary_profile: UserSecretaryProfile | None = None,
) -> GroupingProposal:
    """Accept or reject a secretary grouping proposal."""
    if proposal.state != "awaiting_user_ack":
        raise OmnigentError(
            f"Cannot resolve grouping proposal in state {proposal.state!r}",
            code=ErrorCode.CONFLICT,
        )
    event_ids = task_item_store.list_proposal_event_ids(proposal.id)
    if resolution == "reject_grouping":
        for event_id in event_ids:
            task_event_store.update_event(event_id, state="awaiting_grouping")
        updated = task_item_store.update_grouping_proposal(
            proposal.id,
            state="rejected",
            resolved_at=now_epoch(),
        )
        assert updated is not None
        return updated

    payload = json.loads(proposal.payload)
    groups = payload.get("groups", [])
    if not isinstance(groups, list) or not groups:
        raise OmnigentError("grouping payload must include groups", code=ErrorCode.INVALID_INPUT)

    params = resolve_bootstrap_params(secretary_profile=secretary_profile)
    for group in groups:
        task_id = group.get("attach_to_task_id")
        if task_id is None:
            proposed = group.get("proposed_task") or {}
            manager_agent_id = proposed.get("manager_agent_id")
            title = proposed.get("title")
            if not manager_agent_id or not title:
                raise OmnigentError(
                    "proposed_task requires manager_agent_id and title",
                    code=ErrorCode.INVALID_INPUT,
                )
            new_task_id = _generate_task_id()
            task_store.create(
                new_task_id,
                str(manager_agent_id),
                str(title),
                description=proposed.get("description"),
                charter=proposed.get("charter"),
            )
            task = task_store.get(new_task_id)
            assert task is not None
            bootstrap_task_manager(
                task=task,
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                params=params,
            )
        else:
            loaded = task_store.get(str(task_id))
            if loaded is None:
                raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
            task = loaded

        group_event_ids = [str(event_id) for event_id in group.get("event_ids", [])]
        for event_id in group_event_ids:
            task_event_store.update_event(
                event_id,
                task_id=task.id,
                state="routed",
                manager_agent_id=task.manager_agent_id,
                manager_conversation_id=task.manager_conversation_id,
                routed_at=now_epoch(),
            )

        item_specs = group.get("items", [])
        covered: set[str] = set()
        for item_spec in item_specs:
            item_event_ids = [str(event_id) for event_id in item_spec.get("event_ids", group_event_ids)]
            covered.update(item_event_ids)
            create_task_item(
                task=task,
                task_item_store=task_item_store,
                task_event_store=task_event_store,
                title=str(item_spec.get("title", "Work item")),
                state=str(item_spec.get("initial_state", "awaiting_user_ack")),
                canonical_key=item_spec.get("canonical_key"),
                instructions=item_spec.get("instructions"),
                worker_agent_id=item_spec.get("worker_agent_id"),
                model=item_spec.get("model"),
                host_id=item_spec.get("host_id"),
                workspace=item_spec.get("workspace"),
                harness=item_spec.get("harness"),
                created_by="secretary",
                event_ids=item_event_ids,
            )

        for event_id in group_event_ids:
            if event_id not in covered:
                task_event_store.update_event(
                    event_id,
                    state="reconciled",
                    processed_at=now_epoch(),
                )

    updated = task_item_store.update_grouping_proposal(
        proposal.id,
        state="accepted",
        resolved_at=now_epoch(),
    )
    assert updated is not None
    return updated
