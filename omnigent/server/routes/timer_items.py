"""Routes for deferred timer items (``/v1/timer-items``)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from omnigent.db.utils import now_epoch
from omnigent.entities import TimerItem
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.host.identity import HOST_ID_HEADER
from omnigent.runner.routing import RunnerRouter
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes.sessions import _wake_parent_for_blocked_child
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.timer_item_store import TimerItemStore


class CreateTimerItemRequest(BaseModel):
    """Request body for ``POST /v1/timer-items``."""

    task_type: str
    fire_at: int
    host_id: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_type", "host_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be a non-empty string")
        return stripped


class DispatchPromptRequest(BaseModel):
    """Request body for host-side prompt timer dispatch."""

    session_id: str
    message: str

    @field_validator("session_id", "message")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be a non-empty string")
        return stripped


def _item_to_response(item: TimerItem) -> dict[str, Any]:
    return {
        "object": "timer.item",
        "id": item.id,
        "task_type": item.task_type,
        "fire_at": item.fire_at,
        "state": item.state,
        "host_id": item.host_id,
        "payload": item.payload,
        "owner_user_id": item.owner_user_id,
        "created_at": item.created_at,
        "fired_at": item.fired_at,
    }


def create_timer_items_router(
    timer_item_store: TimerItemStore,
    conversation_store: ConversationStore,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the ``/v1/timer-items`` router."""
    router = APIRouter()

    def _require_host_id(request: Request) -> str:
        host_id = request.headers.get(HOST_ID_HEADER)
        if host_id is None or not host_id.strip():
            raise OmnigentError(
                f"Host timer routes require the {HOST_ID_HEADER} header",
                code=ErrorCode.UNAUTHORIZED,
            )
        return host_id.strip()

    def _runner_router(request: Request) -> RunnerRouter | None:
        return getattr(request.app.state, "runner_router", None)

    @router.post("/timer-items")
    async def create_timer_item(
        request: Request,
        body: CreateTimerItemRequest,
    ) -> dict[str, Any]:
        """Create a deferred timer item for a specific host."""
        user_id = require_user(request, auth_provider)
        item_id = uuid.uuid4().hex

        def _create() -> TimerItem:
            return timer_item_store.create_item(
                item_id,
                body.task_type,
                body.fire_at,
                body.host_id,
                body.payload,
                owner_user_id=user_id,
            )

        item = await asyncio.to_thread(_create)
        return _item_to_response(item)

    @router.get("/timer-items/due")
    async def list_due_timer_items(request: Request) -> dict[str, Any]:
        """List pending timer items due on this host."""
        host_id = _require_host_id(request)
        now = now_epoch()
        items = await asyncio.to_thread(timer_item_store.list_due, host_id, now=now)
        return {"object": "list", "data": [_item_to_response(item) for item in items]}

    @router.post("/timer-items/{item_id}/claim")
    async def claim_timer_item(request: Request, item_id: str) -> dict[str, Any]:
        """Claim one due timer item for execution on this host."""
        host_id = _require_host_id(request)
        item = await asyncio.to_thread(timer_item_store.claim_item, item_id, host_id)
        if item is None:
            raise OmnigentError("Timer item not claimable", code=ErrorCode.NOT_FOUND)
        return _item_to_response(item)

    @router.post("/timer-items/{item_id}/complete")
    async def complete_timer_item(request: Request, item_id: str) -> dict[str, Any]:
        """Mark a running timer item done."""
        host_id = _require_host_id(request)
        item = await asyncio.to_thread(timer_item_store.complete_item, item_id, host_id)
        if item is None:
            raise OmnigentError("Timer item not completable", code=ErrorCode.NOT_FOUND)
        return _item_to_response(item)

    @router.post("/timer-items/{item_id}/fail")
    async def fail_timer_item(request: Request, item_id: str) -> dict[str, Any]:
        """Mark a running timer item failed."""
        host_id = _require_host_id(request)
        item = await asyncio.to_thread(timer_item_store.fail_item, item_id, host_id)
        if item is None:
            raise OmnigentError("Timer item not fail-able", code=ErrorCode.NOT_FOUND)
        return _item_to_response(item)

    @router.post("/timer-items/dispatch-prompt")
    async def dispatch_prompt_timer(
        request: Request, body: DispatchPromptRequest
    ) -> dict[str, bool]:
        """Inject a timer prompt into a session and wake its runner."""
        _require_host_id(request)
        conv = await asyncio.to_thread(
            conversation_store.get_conversation,
            body.session_id,
        )
        if conv is None:
            raise OmnigentError("Session not found", code=ErrorCode.NOT_FOUND)
        delivered = await _wake_parent_for_blocked_child(
            body.session_id,
            conv,
            body.message,
            conversation_store=conversation_store,
            runner_router=_runner_router(request),
        )
        return {"delivered": delivered}

    return router
