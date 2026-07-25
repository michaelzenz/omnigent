"""Secretary task-item routing proposals for ambiguous task events."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

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

_TOKEN_RE = re.compile(r"(?:^|\s)(pr|repo|thread):([^\s]+)", re.IGNORECASE)
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

    suggested_canonical_key: str
    events: list[TaskEvent]


def derive_cluster_key(
    event: TaskEvent,
    tags: list[TaskEventTag] | None = None,
) -> str | None:
    """Return a deterministic cluster key for an ambiguous event."""
    tag_map: dict[str, str] = {}
    if tags:
        for tag in tags:
            tag_map[tag.tag_type] = tag.tag

    repo = tag_map.get("repo")
    pr = tag_map.get("pr")
    if repo and pr:
        return f"pr:{repo}#{pr}"
    thread = tag_map.get("thread")
    if thread:
        return f"thread:{thread}"
    if event.source and event.source_key:
        return f"source:{event.source}:{event.source_key}"

    haystack = "\n".join(
        part for part in (event.summary, event.search_text) if part
    )
    for match in _TOKEN_RE.finditer(haystack):
        kind = match.group(1).lower()
        value = match.group(2)
        if kind == "pr" and repo:
            return f"pr:{repo}#{value}"
        if kind == "repo":
            repo = value
        if kind == "thread":
            return f"thread:{value}"
    if repo and pr:
        return f"pr:{repo}#{pr}"
    return None


def cluster_ambiguous_events(
    events: list[TaskEvent],
    *,
    tags_by_event_id: dict[str, list[TaskEventTag]],
) -> list[AmbiguousEventCluster]:
    """Group ambiguous events by deterministic cluster keys."""
    buckets: dict[str, list[TaskEvent]] = {}
    singletons: list[TaskEvent] = []
    for event in events:
        key = derive_cluster_key(event, tags_by_event_id.get(event.id))
        if key is None:
            singletons.append(event)
        else:
            buckets.setdefault(key, []).append(event)

    clusters = [
        AmbiguousEventCluster(suggested_canonical_key=key, events=rows)
        for key, rows in sorted(buckets.items())
    ]
    for event in singletons:
        clusters.append(
            AmbiguousEventCluster(
                suggested_canonical_key=f"event:{event.id}",
                events=[event],
            ),
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


def _resolve_anchor_task_id(
    *,
    suggested_task_id: str | None,
    candidates: list[dict[str, Any]] | None,
    task_store: TaskStore,
) -> str:
    """Pick an existing task to anchor proposed-task bootstrap metadata."""
    if suggested_task_id is not None:
        if task_store.get(suggested_task_id) is None:
            raise OmnigentError("Suggested task not found", code=ErrorCode.NOT_FOUND)
        return suggested_task_id
    if candidates:
        for row in candidates:
            task_id = str(row.get("task_id", ""))
            if task_id and task_store.get(task_id) is not None:
                return task_id
    active = task_store.list(state="active")
    if not active:
        raise OmnigentError(
            "No active task available to anchor routing proposal",
            code=ErrorCode.INVALID_INPUT,
        )
    return active[0].id


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
    anchor_task: Task,
    title: str,
    proposed_task_id: str | None = None,
    proposed_title: str | None = None,
    proposed_charter: str | None = None,
    proposed_description: str | None = None,
    manager_agent_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    """Return proposed_task metadata, creating a paused task when needed."""
    manager = manager_agent_id or anchor_task.manager_agent_id
    task_id = proposed_task_id or _generate_task_id()
    existing = task_store.get(task_id)
    display_title = proposed_title or f"New: {title}"
    charter = proposed_charter or anchor_task.charter
    if existing is None:
        task_store.create(
            task_id,
            manager,
            display_title,
            owner_user_id=owner_user_id or anchor_task.owner_user_id,
            description=proposed_description,
            charter=charter,
            state="paused",
        )
    elif existing.state == "paused":
        task_store.update(
            task_id,
            title=display_title,
            charter=charter,
            description=proposed_description,
        )
    return {
        "task_id": task_id,
        "title": display_title,
        "charter": charter,
        "description": proposed_description,
        "manager_agent_id": manager,
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


def upsert_routing_proposal(
    *,
    owner_user_id: str,
    canonical_key: str,
    title: str,
    event_ids: list[str],
    task_store: TaskStore,
    task_item_store: TaskItemStore,
    task_event_store: TaskEventStore,
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

    if candidates is None:
        cluster_search = "\n".join(event.search_text for event in events)
        ranked = rank_tasks_for_event(
            event_search_text=cluster_search,
            tasks=task_store.list(state="active"),
        )
        candidates = _candidate_payload(ranked)

    anchor_task_id = _resolve_anchor_task_id(
        suggested_task_id=suggested_task_id,
        candidates=candidates,
        task_store=task_store,
    )
    anchor_task = task_store.get(anchor_task_id)
    assert anchor_task is not None

    proposed_task = _ensure_proposed_task(
        task_store=task_store,
        anchor_task=anchor_task,
        title=title,
        proposed_task_id=proposed_task_id,
        proposed_title=proposed_task_title,
        proposed_charter=proposed_task_charter,
        proposed_description=proposed_task_description,
        manager_agent_id=proposed_task_manager_agent_id,
        owner_user_id=owner_user_id,
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

    existing = task_item_store.get_open_routing_item_by_canonical_key(canonical_key)
    if existing is not None:
        updated = task_item_store.update_item(
            existing.id,
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
            canonical_key=canonical_key,
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
                "suggested_canonical_key": cluster.suggested_canonical_key,
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
            "canonical_key": item.canonical_key,
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
