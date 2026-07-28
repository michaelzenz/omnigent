"""Secretary task-item routing proposals for ambiguous task events."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from omnigent.agent_tasks.agent_builtins import (
    TASK_MANAGER_AGENT_NAME,
    resolve_task_agent_id,
)
from omnigent.agent_tasks.bootstrap import BootstrapParams, bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.constants import (
    AMBIGUOUS_EVENT_STATES,
    ROUTING_PROPOSED_EVENT_STATE,
    ROUTING_PROPOSED_ITEM_STATE,
)
from omnigent.agent_tasks.dispatch import dispatch_worker_for_item, resolve_dispatch_params
from omnigent.agent_tasks.routing import route_event_to_task
from omnigent.agent_tasks.scoring import rank_tasks_for_event
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskEventExecution, TaskEventTag, TaskItem
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore

RoutingResolution = Literal["accept_routing", "reject_routing"]

_CLAIMABLE_EVENT_STATES = frozenset(AMBIGUOUS_EVENT_STATES)


def _generate_item_id() -> str:
    return uuid.uuid4().hex


def _generate_task_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class RoutingCandidate:
    """One scored destination task for a routing proposal."""

    task_id: str
    title: str
    score: float
    reason: str | None = None


@dataclass(frozen=True)
class AmbiguousEventCluster:
    """Suggested ambiguous events bundled for one routing proposal."""

    tags: list[dict[str, str]]
    events: list[TaskEvent]


def _tag_fingerprint(tags: list[TaskEventTag]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((tag.tag_type, tag.tag) for tag in tags))


def _tags_to_payload(tags: list[TaskEventTag]) -> list[dict[str, str]]:
    return [{"tag_type": tag.tag_type, "tag": tag.tag} for tag in tags]


def cluster_ambiguous_events(
    events: list[TaskEvent],
    *,
    tags_by_event_id: dict[str, list[TaskEventTag]],
) -> list[AmbiguousEventCluster]:
    """Group ambiguous events that share the same event tags."""
    buckets: dict[tuple[tuple[str, str], ...], list[TaskEvent]] = {}
    singletons: list[TaskEvent] = []
    for event in events:
        tags = tags_by_event_id.get(event.id, [])
        if not tags:
            singletons.append(event)
            continue
        fingerprint = _tag_fingerprint(tags)
        buckets.setdefault(fingerprint, []).append(event)

    clusters = [
        AmbiguousEventCluster(
            tags=_tags_to_payload(
                tags_by_event_id[rows[0].id],
            ),
            events=rows,
        )
        for rows in buckets.values()
    ]
    clusters.extend(
        AmbiguousEventCluster(tags=[], events=[event]) for event in singletons
    )
    return clusters


def _candidate_payload(ranked: list[tuple[Task, float]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": task.id,
            "title": task.title,
            "score": round(score, 4),
        }
        for task, score in ranked[:5]
    ]


@dataclass(frozen=True)
class _ProposalContext:
    """Bootstrap metadata for a routing proposal when no anchor task exists."""

    manager_agent_id: str
    owner_user_id: str
    charter: str | None


def _charter_from_tags(tags: list[TaskEventTag]) -> str | None:
    for tag in tags:
        if tag.tag_type == "repo":
            return f"repo:{tag.tag}"
    return None


def _resolve_proposal_context(
    *,
    owner_user_id: str,
    suggested_task_id: str | None,
    candidates: list[dict[str, Any]] | None,
    proposed_task_manager_agent_id: str | None,
    proposed_task_charter: str | None,
    charter_hint: str | None,
    task_store: TaskStore,
    agent_store: AgentStore,
) -> _ProposalContext:
    """Resolve manager/owner/charter defaults for a routing proposal."""
    anchor: Task | None = None
    if suggested_task_id is not None:
        anchor = task_store.get(suggested_task_id)
        if anchor is None:
            raise OmnigentError("Suggested task not found", code=ErrorCode.NOT_FOUND)
    elif candidates:
        for row in candidates:
            task_id = str(row.get("task_id", ""))
            if not task_id:
                continue
            anchor = task_store.get(task_id)
            if anchor is not None:
                break

    if anchor is not None:
        return _ProposalContext(
            manager_agent_id=anchor.manager_agent_id,
            owner_user_id=anchor.owner_user_id or owner_user_id,
            charter=proposed_task_charter or anchor.charter,
        )

    manager_agent_id = proposed_task_manager_agent_id
    if not manager_agent_id:
        manager_agent_id = resolve_task_agent_id(agent_store, TASK_MANAGER_AGENT_NAME)

    return _ProposalContext(
        manager_agent_id=manager_agent_id,
        owner_user_id=owner_user_id,
        charter=proposed_task_charter or charter_hint,
    )


def _build_routing_proposal_json(
    *,
    owner_user_id: str,
    suggested_task_id: str | None,
    candidates: list[dict[str, Any]],
    rationale: str | None,
    proposed_task: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "owner_user_id": owner_user_id,
            "suggested_task_id": suggested_task_id,
            "candidates": candidates,
            "rationale": rationale,
            "proposed_task": proposed_task,
        },
    )


def _parse_routing_proposal(payload: str | None) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OmnigentError("routing_proposal must be valid JSON", code=ErrorCode.INVALID_INPUT) from exc
    if not isinstance(parsed, dict):
        raise OmnigentError("routing_proposal must be a JSON object", code=ErrorCode.INVALID_INPUT)
    return parsed


def _claimable_events(
    event_ids: list[str],
    *,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
) -> list[TaskEvent]:
    claimed: list[TaskEvent] = []
    for event_id in event_ids:
        if task_item_store.get_routing_item_for_event(event_id) is not None:
            continue
        if task_item_store.get_fyi_cluster_for_event(event_id) is not None:
            continue
        event = task_event_store.get_event(event_id)
        if event is None:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        if event.state not in _CLAIMABLE_EVENT_STATES:
            continue
        claimed.append(event)
    return claimed


def _ensure_proposed_task(
    *,
    task_store: TaskStore,
    manager_agent_id: str,
    owner_user_id: str,
    charter: str | None,
    title: str,
    proposed_task_id: str | None = None,
    proposed_title: str | None = None,
    proposed_charter: str | None = None,
    proposed_description: str | None = None,
) -> dict[str, Any]:
    """Return proposed_task metadata, creating a paused task when needed."""
    task_id = proposed_task_id or _generate_task_id()
    existing = task_store.get(task_id)
    display_title = proposed_title or f"New: {title}"
    resolved_charter = proposed_charter or charter
    if existing is None:
        task_store.create(
            task_id,
            manager_agent_id,
            display_title,
            owner_user_id=owner_user_id,
            description=proposed_description,
            charter=resolved_charter,
            state="paused",
        )
    elif existing.state == "paused":
        task_store.update(
            task_id,
            title=display_title,
            charter=resolved_charter,
            description=proposed_description,
        )
    return {
        "task_id": task_id,
        "title": display_title,
        "charter": resolved_charter,
        "description": proposed_description,
        "manager_agent_id": manager_agent_id,
        "is_new": True,
    }


def _archive_task_if_paused(task_store: TaskStore, task_id: str) -> None:
    task = task_store.get(task_id)
    if task is not None and task.state == "paused":
        task_store.update(task_id, state="archived")


def _activate_task_if_paused(
    task_store: TaskStore,
    task_id: str,
    *,
    title: str | None = None,
    charter: str | None = None,
    description: str | None = None,
) -> Task:
    task = task_store.get(task_id)
    if task is None:
        raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
    if task.state == "paused":
        updated = task_store.update(
            task_id,
            state="active",
            title=title,
            charter=charter,
            description=description,
        )
        assert updated is not None
        return updated
    if title is not None or charter is not None or description is not None:
        updated = task_store.update(
            task_id,
            title=title,
            charter=charter,
            description=description,
        )
        if updated is not None:
            return updated
    return task


def create_routing_proposal(
    *,
    owner_user_id: str,
    title: str,
    event_ids: list[str],
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    agent_store: AgentStore,
    item_id: str | None = None,
    suggested_task_id: str | None = None,
    instructions: str | None = None,
    worker_agent_id: str | None = None,
    model: str | None = None,
    host_id: str | None = None,
    workspace: str | None = None,
    harness: str | None = None,
    rationale: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
    proposed_task_id: str | None = None,
    proposed_task_title: str | None = None,
    proposed_task_charter: str | None = None,
    proposed_task_description: str | None = None,
    proposed_task_manager_agent_id: str | None = None,
) -> TaskItem | None:
    """Create or extend a secretary routing proposal for ambiguous events."""
    events = _claimable_events(
        event_ids,
        task_event_store=task_event_store,
        task_item_store=task_item_store,
    )
    if not events:
        return None

    event_tags: list[TaskEventTag] = []
    for event in events:
        event_tags.extend(task_event_store.get_event_tags(event.id))

    if candidates is None:
        cluster_search = "\n".join(event.search_text for event in events)
        ranked = rank_tasks_for_event(
            event_search_text=cluster_search,
            tasks=task_store.list(state="active"),
        )
        candidates = _candidate_payload(ranked)

    proposal_context = _resolve_proposal_context(
        owner_user_id=owner_user_id,
        suggested_task_id=suggested_task_id,
        candidates=candidates,
        proposed_task_manager_agent_id=proposed_task_manager_agent_id,
        proposed_task_charter=proposed_task_charter,
        charter_hint=_charter_from_tags(event_tags),
        task_store=task_store,
        agent_store=agent_store,
    )

    proposed_task = _ensure_proposed_task(
        task_store=task_store,
        manager_agent_id=proposal_context.manager_agent_id,
        owner_user_id=proposal_context.owner_user_id,
        charter=proposal_context.charter,
        title=title,
        proposed_task_id=proposed_task_id,
        proposed_title=proposed_task_title,
        proposed_charter=proposed_task_charter,
        proposed_description=proposed_task_description,
    )

    item_task_id = (
        suggested_task_id
        if suggested_task_id is not None
        else str(proposed_task["task_id"])
    )

    proposal_json = _build_routing_proposal_json(
        owner_user_id=owner_user_id,
        suggested_task_id=suggested_task_id,
        candidates=candidates,
        rationale=rationale,
        proposed_task=proposed_task,
    )

    if item_id is not None:
        existing = task_item_store.get_item(item_id)
        if existing is None:
            raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
        if existing.state != ROUTING_PROPOSED_ITEM_STATE:
            raise OmnigentError(
                f"Cannot extend item in state {existing.state!r}",
                code=ErrorCode.CONFLICT,
            )
        updated = task_item_store.update_item(
            item_id,
            title=title,
            instructions=instructions,
            worker_agent_id=worker_agent_id,
            model=model,
            host_id=host_id,
            workspace=workspace,
            harness=harness,
            task_id=item_task_id,
            routing_proposal=proposal_json,
        )
        assert updated is not None
        item = updated
    else:
        item = task_item_store.create_item(
            _generate_item_id(),
            item_task_id,
            title,
            state=ROUTING_PROPOSED_ITEM_STATE,
            instructions=instructions,
            worker_agent_id=worker_agent_id,
            model=model,
            host_id=host_id,
            workspace=workspace,
            harness=harness,
            created_by="secretary",
            routing_proposal=proposal_json,
        )

    for event in events:
        task_item_store.link_event(item.id, event.id, relation="proposed")
        task_event_store.update_event(event.id, state=ROUTING_PROPOSED_EVENT_STATE)
    return item


def build_ambiguous_inbox(
    *,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
    task_store: TaskStore,
) -> dict[str, Any]:
    """Return ambiguous events and suggested clusters for secretary reconcile."""
    ambiguous_events = []
    for event in task_event_store.list_events(state="awaiting_grouping"):
        if task_item_store.get_routing_item_for_event(event.id) is not None:
            continue
        if task_item_store.get_fyi_cluster_for_event(event.id) is not None:
            continue
        ambiguous_events.append(event)

    tags_by_event_id: dict[str, list[TaskEventTag]] = {}
    for event in ambiguous_events:
        tags_by_event_id[event.id] = task_event_store.get_event_tags(event.id)

    clusters = cluster_ambiguous_events(ambiguous_events, tags_by_event_id=tags_by_event_id)
    active_tasks = task_store.list(state="active")
    rendered_clusters: list[dict[str, Any]] = []
    for cluster in clusters:
        cluster_search = "\n".join(event.search_text for event in cluster.events)
        ranked = rank_tasks_for_event(event_search_text=cluster_search, tasks=active_tasks)
        candidate_payload = _candidate_payload(ranked)
        rendered_clusters.append(
            {
                "tags": cluster.tags,
                "events": [_event_summary(event) for event in cluster.events],
                "suggested_candidates": candidate_payload,
            },
        )

    return {
        "object": "agent.task.ambiguous_inbox",
        "clusters": rendered_clusters,
        "unclustered_count": 0,
    }


def list_routing_decision_cards(
    *,
    owner_user_id: str | None,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    task_store: TaskStore,
) -> list[dict[str, Any]]:
    """Build board decision cards for open routing proposals."""
    items = task_item_store.list_items_by_state(
        ROUTING_PROPOSED_ITEM_STATE,
        created_by="secretary",
    )
    cards: list[dict[str, Any]] = []
    for item in items:
        proposal = _parse_routing_proposal(item.routing_proposal)
        proposal_owner = proposal.get("owner_user_id")
        if (
            owner_user_id is not None
            and proposal_owner not in {owner_user_id, "__anonymous__", None}
        ):
            continue
        cards.append(
            _routing_card_payload(
                item=item,
                proposal=proposal,
                task_item_store=task_item_store,
                task_event_store=task_event_store,
                task_store=task_store,
            ),
        )
    return cards


def list_board_triage(
    *,
    owner_user_id: str | None,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    task_store: TaskStore,
) -> dict[str, Any]:
    """Return routing decision cards and FYI clusters for the board."""
    from omnigent.agent_tasks.fyi_clusters import list_fyi_board_cards

    return {
        "object": "agent.task.board",
        "decisions": list_routing_decision_cards(
            owner_user_id=owner_user_id,
            task_item_store=task_item_store,
            task_event_store=task_event_store,
            task_store=task_store,
        ),
        "fyi": list_fyi_board_cards(
            owner_user_id=owner_user_id,
            task_item_store=task_item_store,
            task_event_store=task_event_store,
        ),
    }


def _routing_card_payload(
    *,
    item: TaskItem,
    proposal: dict[str, Any],
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    task_store: TaskStore,
) -> dict[str, Any]:
    event_links = task_item_store.list_events_for_item(item.id)
    events = []
    for link in event_links:
        event = task_event_store.get_event(link.event_id)
        if event is not None:
            events.append(_event_summary(event))

    suggested_task_id = proposal.get("suggested_task_id")
    proposed_task = proposal.get("proposed_task") or {}
    proposed_task_id = str(proposed_task.get("task_id", ""))
    default_task_id = (
        suggested_task_id
        if suggested_task_id is not None
        else proposed_task_id
    )
    candidates = []
    seen_ids: set[str] = set()
    for row in proposal.get("candidates", []):
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("task_id", ""))
        if not task_id or task_id in seen_ids:
            continue
        seen_ids.add(task_id)
        task = task_store.get(task_id)
        candidates.append(
            {
                "task_id": task_id,
                "task_title": task.title if task is not None else task_id,
                "score": row.get("score"),
                "recommended": task_id == default_task_id,
                "is_new": False,
            },
        )

    if proposed_task_id and proposed_task_id not in seen_ids:
        proposed_title = str(proposed_task.get("title") or "Create new task")
        candidates.append(
            {
                "task_id": proposed_task_id,
                "task_title": proposed_title,
                "score": None,
                "recommended": proposed_task_id == default_task_id,
                "is_new": True,
            },
        )

    return {
        "id": item.id,
        "kind": "task_item_routing",
        "state": "pending",
        "created_at": item.created_at,
        "resolved_at": None,
        "headline": item.title,
        "rationale": proposal.get("rationale"),
        "body": {
            "title": item.title,
            "instructions": item.instructions,
            "suggested_task_id": suggested_task_id,
            "proposed_task": proposed_task,
            "events": events,
            "candidates": candidates,
            "worker_agent_id": item.worker_agent_id,
            "model": item.model,
            "harness": item.harness,
            "host_id": item.host_id,
            "workspace": item.workspace,
        },
    }


def _event_summary(event: TaskEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "title": event.title,
        "summary": event.summary,
        "state": event.state,
        "source": event.source,
        "source_key": event.source_key,
        "created_at": event.created_at,
    }


async def resolve_routing_proposal(
    *,
    item: TaskItem,
    resolution: RoutingResolution,
    selected_task_id: str | None,
    instructions: str | None = None,
    proposed_task_title: str | None = None,
    proposed_task_charter: str | None = None,
    proposed_task_description: str | None = None,
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    secretary_profile: UserSecretaryProfile | None = None,
) -> tuple[TaskItem, TaskEventExecution | None]:
    """Accept or reject a secretary routing proposal."""
    if item.state != ROUTING_PROPOSED_ITEM_STATE:
        raise OmnigentError(
            f"Cannot resolve item in state {item.state!r}",
            code=ErrorCode.CONFLICT,
        )

    proposal = _parse_routing_proposal(item.routing_proposal)
    proposed_task = proposal.get("proposed_task") or {}
    proposed_task_id = str(proposed_task.get("task_id", ""))

    if resolution == "reject_routing":
        for link in task_item_store.list_events_for_item(item.id):
            event = task_event_store.get_event(link.event_id)
            if event is not None and event.state == ROUTING_PROPOSED_EVENT_STATE:
                task_event_store.update_event(event.id, state="awaiting_grouping")
        if proposed_task_id:
            _archive_task_if_paused(task_store, proposed_task_id)
        updated = task_item_store.update_item(item.id, state="cancelled")
        assert updated is not None
        return updated, None

    if selected_task_id is None:
        raise OmnigentError("selected_task_id is required", code=ErrorCode.INVALID_INPUT)

    if proposed_task_id and selected_task_id != proposed_task_id:
        _archive_task_if_paused(task_store, proposed_task_id)

    if proposed_task_id and selected_task_id == proposed_task_id:
        task = _activate_task_if_paused(
            task_store,
            selected_task_id,
            title=proposed_task_title or proposed_task.get("title"),
            charter=proposed_task_charter or proposed_task.get("charter"),
            description=proposed_task_description or proposed_task.get("description"),
        )
    else:
        task = task_store.get(selected_task_id)
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)

    params = resolve_bootstrap_params(
        host_id=item.host_id,
        workspace=item.workspace,
        harness=item.harness,
        model=item.model,
        secretary_profile=secretary_profile,
    )
    bootstrapped = bootstrap_task_manager(
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        params=params,
    )

    if item.task_id != bootstrapped.id:
        moved = task_item_store.update_item(item.id, task_id=bootstrapped.id)
        assert moved is not None
        item = moved

    if instructions is not None and instructions != (item.instructions or ""):
        updated_instructions = task_item_store.update_item(
            item.id,
            instructions=instructions,
        )
        assert updated_instructions is not None
        item = updated_instructions

    linked = task_item_store.list_events_for_item(item.id)
    for link in linked:
        event = task_event_store.get_event(link.event_id)
        if event is None:
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
        if event.state != ROUTING_PROPOSED_EVENT_STATE:
            raise OmnigentError(
                f"Cannot accept routing for event in state {event.state!r}",
                code=ErrorCode.CONFLICT,
            )
        route_event_to_task(
            event=event,
            task=bootstrapped,
            task_store=task_store,
            task_event_store=task_event_store,
            conversation_store=conversation_store,
            agent_store=agent_store,
            params=params,
        )
        task_event_store.update_event(
            link.event_id,
            state="reconciled",
            processed_at=now_epoch(),
        )

    dispatch_params = resolve_dispatch_params(
        payload={
            "worker_agent_id": item.worker_agent_id,
            "title": item.title,
            "instructions": item.instructions or "",
            "host_id": item.host_id,
            "workspace": item.workspace,
            "harness": item.harness,
            "model": item.model,
        },
        secretary_profile=secretary_profile,
    )
    execution, _worker_id = dispatch_worker_for_item(
        task=bootstrapped,
        item=item,
        params=dispatch_params,
        task_item_store=task_item_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
    )
    updated = task_item_store.get_item(item.id)
    assert updated is not None
    return updated, execution
