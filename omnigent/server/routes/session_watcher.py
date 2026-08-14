"""Routes for the session watcher (``/v1/session-watcher``).

The update endpoint is separate from the generic ``/v1/task-events`` path so
the server can respond with a ``track`` flag — telling the watcher plugin
whether to keep polling the session or drop it from its tracking list.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from omnigent.agent_tasks.event_types import EXTERNAL_SESSION_UPDATED_EVENT_TYPE
from omnigent.agent_tasks.ingress import ingress_event
from omnigent.entities import EventTag
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import get_user_id
from omnigent.server.routes.task_events import HOST_ID_HEADER
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore

_logger = logging.getLogger(__name__)


class SessionWatcherUpdateRequest(BaseModel):
    """Request body for ``POST /v1/session-watcher/update``."""

    session_hint: str
    history_hash: str | None = None
    rewind_at: str | None = None
    transcript_delta: str | None = None
    transcript_snippet: str | None = None
    payload: dict[str, Any] | None = None


def create_session_watcher_router(
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    task_role_profile_store: TaskRoleProfileStore | None = None,
    auth_provider: Any | None = None,
    session_creator: Any | None = None,
) -> APIRouter:
    """Build the session-watcher router."""
    router = APIRouter()

    def _effective_user_id(user_id: str | None) -> str:
        return user_id if user_id is not None else "__anonymous__"

    async def _load_broker_profile() -> Any:
        if task_role_profile_store is None:
            return None
        from omnigent.agent_tasks.broker_role_profile import TASK_BROKER_ROLE

        return await asyncio.to_thread(task_role_profile_store.get, TASK_BROKER_ROLE)

    @router.post("/session-watcher/update")
    async def session_watcher_update(
        request: Request,
        body: SessionWatcherUpdateRequest,
    ) -> dict[str, Any]:
        """Accept a transcript update from a watcher plugin.

        Creates a task event, runs ingress (auto-routes to the task if the
        session is adopted), and returns ``track`` so the plugin knows whether
        to keep polling.
        """
        user_id = get_user_id(request, auth_provider)
        poller_host_id = request.headers.get(HOST_ID_HEADER)
        if poller_host_id is not None:
            poller_host_id = poller_host_id.strip() or None
        if user_id is None and poller_host_id is None:
            from omnigent.server.auth import require_user

            user_id = require_user(request, auth_provider)

        owner = _effective_user_id(user_id)

        # Check if the session is adopted (worker exists with this hint).
        worker = await asyncio.to_thread(
            worker_store.get_by_external_hint, body.session_hint
        )
        track = worker is not None

        # Build the event payload.
        event_payload: dict[str, Any] = {
            "session_hint": body.session_hint,
            "history_hash": body.history_hash,
        }
        if body.rewind_at is not None:
            event_payload["rewind_at"] = body.rewind_at
        if body.transcript_delta is not None:
            event_payload["transcript_delta"] = body.transcript_delta
        if body.transcript_snippet is not None:
            event_payload["transcript_snippet"] = body.transcript_snippet
        if body.payload:
            event_payload["extra"] = body.payload

        event_id = uuid.uuid4().hex
        title = f"External session update for {body.session_hint}"
        if body.rewind_at is not None:
            title = f"External session rewind for {body.session_hint}"

        task_id = worker.task_id if worker is not None else None

        def _create() -> Any:
            return task_event_store.create_event(
                event_id,
                EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
                title,
                payload=json.dumps(event_payload),
                source="session_watcher",
                source_key=body.session_hint,
                source_offset=None,
                task_id=task_id,
                state="received",
                tags=[],
                owner_user_id=owner,
            )

        created = await asyncio.to_thread(_create)
        profile = await _load_broker_profile()
        distributed = await ingress_event(
            event=created,
            task_store=task_store,
            task_event_store=task_event_store,
            worker_store=worker_store,
            conversation_store=conversation_store,
            task_role_profile_store=task_role_profile_store,
            role_profile=profile,
            owner_user_id=owner,
            session_creator=session_creator,
            app_state=request.app.state,
            user_id=user_id,
        )

        return {
            "track": track,
            "event_id": distributed.id,
            "state": distributed.state,
        }

    return router
