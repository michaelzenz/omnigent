"""The keys that the caller's GlobalHub tasks follow (``/v1/agent-tasks/.../subscriptions``).

An event whose key a live task follows goes to the task's manager; other events
go to the broker. Include this router before the tasks router:
``/agent-tasks/{task_id}`` would otherwise match ``/agent-tasks/subscriptions``.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import TaskSubscription
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    AgentTaskSubscriptionDeletedResponse,
    AgentTaskSubscriptionList,
    AgentTaskSubscriptionResponse,
    CreateAgentTaskSubscriptionRequest,
)
from omnigent.stores.task_subscription_store import SourceKeyError


def create_agent_task_subscriptions_router(
    board: BoardStores,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the keys that the caller's tasks follow.

    :param board: The board's tasks and their subscriptions.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    tasks = board.tasks
    subscriptions = board.subscriptions

    def require_owned_task(task_id: str, user_id: str | None) -> str:
        task = tasks.get(task_id, owner_user_id=user_id)
        if task is None:
            raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)
        return task.id

    @router.get("/agent-tasks/subscriptions", response_model=AgentTaskSubscriptionList)
    async def list_followed_keys(request: Request) -> AgentTaskSubscriptionList:
        """List the keys that the caller's live tasks follow, oldest first.

        A watcher reads it to find what to check, e.g. the pull requests that
        live tasks follow.
        """
        user_id = require_user(request, auth_provider)
        listed = await asyncio.to_thread(subscriptions.list_live, owner_user_id=user_id)
        return AgentTaskSubscriptionList(data=[subscription_response(item) for item in listed])

    @router.post(
        "/agent-tasks/{task_id}/subscriptions", response_model=AgentTaskSubscriptionResponse
    )
    async def subscribe_task(
        request: Request, task_id: str, body: CreateAgentTaskSubscriptionRequest
    ) -> AgentTaskSubscriptionResponse:
        """Make one of the caller's tasks follow a key, or return the subscription it has."""
        user_id = require_user(request, auth_provider)

        def subscribe() -> TaskSubscription:
            task = require_owned_task(task_id, user_id)
            try:
                return subscriptions.subscribe(
                    uuid.uuid4().hex,
                    owner_user_id=user_id,
                    task_id=task,
                    source_key=body.source_key,
                    source=body.source,
                )
            except SourceKeyError as exc:
                raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc

        return subscription_response(await asyncio.to_thread(subscribe))

    @router.get("/agent-tasks/{task_id}/subscriptions", response_model=AgentTaskSubscriptionList)
    async def list_task_subscriptions(request: Request, task_id: str) -> AgentTaskSubscriptionList:
        """List the keys that one of the caller's tasks follows, oldest first.

        The task's worker sessions are not listed here; the worker roster lists them.
        """
        user_id = require_user(request, auth_provider)

        def load() -> list[TaskSubscription]:
            task = require_owned_task(task_id, user_id)
            return subscriptions.list_for_task(task, owner_user_id=user_id)

        listed = await asyncio.to_thread(load)
        return AgentTaskSubscriptionList(data=[subscription_response(item) for item in listed])

    @router.delete(
        "/agent-tasks/{task_id}/subscriptions/{subscription_id}",
        response_model=AgentTaskSubscriptionDeletedResponse,
    )
    async def unsubscribe_task(
        request: Request, task_id: str, subscription_id: str
    ) -> AgentTaskSubscriptionDeletedResponse:
        """Stop one of the caller's tasks from following a key."""
        user_id = require_user(request, auth_provider)
        deleted = await asyncio.to_thread(
            subscriptions.delete, subscription_id, owner_user_id=user_id, task_id=task_id
        )
        if not deleted:
            raise OmnigentError("subscription not found", code=ErrorCode.NOT_FOUND)
        return AgentTaskSubscriptionDeletedResponse(id=subscription_id, task_id=task_id)

    return router


def subscription_response(subscription: TaskSubscription) -> AgentTaskSubscriptionResponse:
    return AgentTaskSubscriptionResponse(
        id=subscription.id,
        task_id=subscription.task_id,
        source_key=subscription.source_key,
        source=subscription.source,
        created_at=subscription.created_at,
    )
