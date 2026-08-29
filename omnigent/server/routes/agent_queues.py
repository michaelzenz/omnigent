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
        """Re-arm a paused or halted agent queue. User-only."""
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

        For a dispatch-failed item — the one that halted the queue — cancel also
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
