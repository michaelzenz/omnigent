"""Control-plane routes for agent queues (``/v1/agent-queues``).

The store-level capability landed in phase 0; this is the minimal HTTP surface.
``/resume`` is the load-bearing one — it is the only recovery path for a worker
slot halted by a failed dispatch, so it must exist before worker dispatch ships.
Resume and pause are user-only: a manager agent must not clear a halt it may have
caused, so callers cannot target a queue they do not own.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from omnigent.agent_tasks.agent_builtins import TASK_BROKER_ROLE, TASK_MANAGER_ROLE
from omnigent.agent_tasks.constants import TERMINAL_EVENT_STATES
from omnigent.db.utils import now_epoch
from omnigent.entities import AgentQueueKey
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.agent_queue_store import AgentQueueStore


class QueueKeyRequest(BaseModel):
    """The identity of one agent queue."""

    owner_user_id: str = ""
    scope_id: str | None = None


class PatchQueueItemRequest(BaseModel):
    """Editable fields on a queued item, before dispatch."""

    payload: str | None = None


class DispatchStoplistRequest(BaseModel):
    """One dispatch stoplist entry.

    ``scope_id`` narrows the stop to a single queue of the role (e.g. one
    manager's queue); omitted, the whole role is stopped.
    """

    role: str
    stopped: bool
    scope_id: str | None = None


class DismissBacklogRequest(BaseModel):
    """Identifies the queue whose waiting events should be dismissed."""

    role: str
    scope_id: str | None = None


def create_agent_queues_router(
    agent_queue_store: AgentQueueStore,
    auth_provider: AuthProvider | None = None,
    *,
    task_event_store: Any = None,
) -> APIRouter:
    """Build the agent-queue control-plane router."""
    router = APIRouter()

    def _key(role: str, body: QueueKeyRequest) -> AgentQueueKey:
        return AgentQueueKey(
            role=role,
            owner_user_id=body.owner_user_id,
            scope_id=body.scope_id,
        )

    @router.get("/agent-queues")
    async def list_queues(request: Request, state: str | None = None) -> dict[str, Any]:
        """List agent queues, optionally filtered by state."""
        require_user(request, auth_provider)
        queues = await asyncio.to_thread(
            agent_queue_store.list_queues,
            state=state,
        )
        return {
            "object": "list",
            "data": [_queue_to_response(q) for q in queues],
        }

    @router.get("/agent-queues/dispatch-stoplist")
    async def get_dispatch_stoplist(request: Request) -> dict[str, Any]:
        """Stop keys the dispatcher currently refuses to dispatch.

        Keys are bare roles ("broker") or scope-qualified roles
        ("manager:<manager_id>") for a single queue.
        """
        require_user(request, auth_provider)
        stopped = await asyncio.to_thread(agent_queue_store.get_dispatch_stoplist)
        return {"object": "list", "data": sorted(stopped)}

    @router.put("/agent-queues/dispatch-stoplist")
    async def set_dispatch_stoplist(
        request: Request,
        body: DispatchStoplistRequest,
    ) -> dict[str, Any]:
        """Add an entry to (or remove it from) the global dispatch stoplist.

        The dispatcher skips a stopped target entirely: its queues keep
        their items queued and resume on their own when re-enabled. With
        ``scope_id`` set, only that one queue of the role is stopped.
        """
        require_user(request, auth_provider)
        stop_key = f"{body.role}:{body.scope_id}" if body.scope_id else body.role
        await asyncio.to_thread(
            agent_queue_store.set_role_dispatch_stopped,
            stop_key,
            body.stopped,
        )
        return {"role": body.role, "scope_id": body.scope_id, "stopped": body.stopped}

    @router.get("/agent-queues/event-backlog")
    async def get_event_backlog() -> dict[str, Any]:
        """Count events waiting per dispatch queue.

        The broker's queue holds ``awaiting_grouping`` + ``pending_triage``
        events; each manager's queue holds the ``routed`` events addressed
        to it. Leftover non-terminal states (transient ingress states, the
        FYI bucket) are reported under ``other`` — they are not dispatched.
        """
        if task_event_store is None:
            return {"object": "list", "data": [], "other": {}}
        counts = await asyncio.to_thread(task_event_store.count_events_by_state)
        by_manager = await asyncio.to_thread(task_event_store.count_routed_events_by_manager)
        broker_count = counts.get("awaiting_grouping", 0) + counts.get("pending_triage", 0)
        data: list[dict[str, Any]] = [{"role": "broker", "scope_id": None, "count": broker_count}]
        data.extend(
            {"role": "manager", "scope_id": manager_id, "count": count}
            for manager_id, count in sorted(by_manager.items())
        )
        routed_unassigned = counts.get("routed", 0) - sum(by_manager.values())
        other = {
            state: count
            for state, count in counts.items()
            if state not in TERMINAL_EVENT_STATES
            and state not in {"awaiting_grouping", "pending_triage", "routed"}
        }
        if routed_unassigned > 0:
            other["routed_unassigned"] = routed_unassigned
        return {"object": "list", "data": data, "other": other}

    @router.post("/agent-queues/dismiss-backlog")
    async def dismiss_queue_backlog(
        request: Request,
        body: DismissBacklogRequest,
    ) -> dict[str, Any]:
        """Dismiss every event waiting on one dispatch queue. User-only, bulk.

        The broker's queue covers ``awaiting_grouping`` + ``pending_triage``;
        a manager's queue covers the ``routed`` events addressed to it.
        Queued (not yet dispatched) notices for that queue are cancelled so
        stale prompts are not delivered; in-flight items finish naturally.
        """
        require_user(request, auth_provider)
        if task_event_store is None:
            raise OmnigentError(
                "task event store is not configured on this server",
                code=ErrorCode.INTERNAL_ERROR,
            )
        if body.role == TASK_BROKER_ROLE:
            events = [
                event
                for state in ("awaiting_grouping", "pending_triage")
                for event in await asyncio.to_thread(
                    task_event_store.list_events,
                    state=state,
                )
            ]
        elif body.role == TASK_MANAGER_ROLE:
            if body.scope_id is None:
                raise OmnigentError(
                    "scope_id is required to dismiss a manager's backlog",
                    code=ErrorCode.INVALID_INPUT,
                )
            events = [
                event
                for event in await asyncio.to_thread(
                    task_event_store.list_events,
                    state="routed",
                )
                if event.manager_id == body.scope_id
            ]
        else:
            raise OmnigentError(
                f"unsupported queue role: {body.role}",
                code=ErrorCode.INVALID_INPUT,
            )

        # Cancel queued notices for the queue so dismissed events are not
        # delivered as stale prompts. In-flight items finish naturally.
        cancelled = 0
        if agent_queue_store is not None:
            for queue in await asyncio.to_thread(
                agent_queue_store.list_queues,
                role=body.role,
            ):
                if body.role == TASK_MANAGER_ROLE and queue.scope_id != body.scope_id:
                    continue
                key = AgentQueueKey(
                    role=queue.role,
                    owner_user_id=queue.owner_user_id,
                    scope_id=queue.scope_id,
                )
                for item in await asyncio.to_thread(
                    agent_queue_store.list_items,
                    key,
                    state="queued",
                ):
                    await asyncio.to_thread(
                        agent_queue_store.cancel_item,
                        item.id,
                        now=now_epoch(),
                    )
                    cancelled += 1

        dismissed = await asyncio.to_thread(
            task_event_store.dismiss_events,
            [event.id for event in events],
        )
        return {"dismissed": dismissed, "cancelled_items": cancelled}

    @router.get("/agent-queues/{role}/items")
    async def list_queue_items(
        request: Request,
        role: str,
        owner_user_id: str = "",
        scope_id: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Inspect pending work for one agent queue."""
        require_user(request, auth_provider)
        key = AgentQueueKey(role=role, owner_user_id=owner_user_id, scope_id=scope_id)
        items = await asyncio.to_thread(
            agent_queue_store.list_items,
            key,
            state=state,
        )
        return {
            "object": "list",
            "data": [_item_to_response(i) for i in items],
        }

    @router.post("/agent-queues/{role}/pause")
    async def pause_queue(
        request: Request,
        role: str,
        body: QueueKeyRequest,
    ) -> dict[str, Any]:
        """Stop feeding an agent. User-only; clears on resume."""
        require_user(request, auth_provider)
        key = _key(role, body)
        await asyncio.to_thread(agent_queue_store.set_queue_state, key, "paused")
        queue = agent_queue_store.get_queue(key)
        return _queue_to_response(queue) if queue is not None else {"paused": True}

    @router.post("/agent-queues/{role}/resume")
    async def resume_queue(
        request: Request,
        role: str,
        body: QueueKeyRequest,
    ) -> dict[str, Any]:
        """Re-arm a paused agent queue. User-only."""
        require_user(request, auth_provider)
        key = _key(role, body)
        await asyncio.to_thread(agent_queue_store.set_queue_state, key, "active")
        queue = agent_queue_store.get_queue(key)
        return _queue_to_response(queue) if queue is not None else {"resumed": True}

    @router.patch("/agent-queue-items/{item_id}")
    async def patch_queue_item(
        request: Request,
        item_id: str,
        body: PatchQueueItemRequest,
    ) -> dict[str, Any]:
        """Edit a queued item's payload before dispatch. User-only.

        Rejects items that already left the queue (dispatched, done, cancelled,
        or dispatch-failed), so a payload cannot change out from under a running
        agent.
        """
        require_user(request, auth_provider)
        item = await asyncio.to_thread(
            agent_queue_store.update_item,
            item_id,
            payload=body.payload,
        )
        if item is None:
            raise OmnigentError(
                "Queue item not found or already dispatched",
                code=ErrorCode.NOT_FOUND,
            )
        return _item_to_response(item)

    @router.post("/agent-queue-items/{item_id}/cancel")
    async def cancel_queue_item(
        request: Request,
        item_id: str,
    ) -> dict[str, Any]:
        """Drop a queued or dispatch-failed item. User-only.

        For a parked item — cancel also
        clears the halt, so it is a complete recovery and not a two-step resume.
        Idempotent: an already-terminal item returns its current state.
        """
        require_user(request, auth_provider)
        item = await asyncio.to_thread(
            agent_queue_store.cancel_item,
            item_id,
            now=now_epoch(),
        )
        if item is None:
            raise OmnigentError(
                "Queue item not found",
                code=ErrorCode.NOT_FOUND,
            )
        # Release source events back to awaiting_grouping so the packager
        # can re-package them.
        if task_event_store is not None and item.source_ids:
            for sid in item.source_ids:
                await asyncio.to_thread(
                    task_event_store.update_event,
                    sid,
                    state="awaiting_grouping",
                )
        return _item_to_response(item)

    return router


def _queue_to_response(queue: Any) -> dict[str, Any]:
    return {
        "object": "agent_queue",
        "role": queue.role,
        "owner_user_id": queue.owner_user_id,
        "scope_id": queue.scope_id,
        "state": queue.state,
        "conversation_id": queue.conversation_id,
        "inflight_item_id": queue.inflight_item_id,
        "last_error": queue.last_error,
    }


def _item_to_response(item: Any) -> dict[str, Any]:
    return {
        "object": "agent_queue_item",
        "id": item.id,
        "role": item.role,
        "owner_user_id": item.owner_user_id,
        "scope_id": item.scope_id,
        "kind": item.kind,
        "state": item.state,
        "source_ids": item.source_ids,
        "payload": item.payload,
        "seq": item.seq,
        "not_before": item.not_before,
        "last_error": item.last_error,
    }
