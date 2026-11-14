"""Links the caller's GlobalHub tasks carry (``/v1/agent-tasks/{task_id}/assets``)."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.entities import TaskAsset
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    AgentTaskAssetDeletedResponse,
    AgentTaskAssetResponse,
    CreateAgentTaskAssetRequest,
)


def create_agent_task_assets_router(
    board: BoardStores,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the links the caller's tasks carry.

    :param board: The board's tasks and their assets.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    tasks = board.tasks
    assets = board.assets

    @router.post("/agent-tasks/{task_id}/assets", response_model=AgentTaskAssetResponse)
    async def attach_asset(
        request: Request, task_id: str, body: CreateAgentTaskAssetRequest
    ) -> AgentTaskAssetResponse:
        """Attach a link to one of the caller's tasks, or update the one it has for the URL."""
        user_id = require_user(request, auth_provider)

        def attach() -> TaskAsset:
            task = tasks.get(task_id, owner_user_id=user_id)
            if task is None:
                raise OmnigentError("task not found", code=ErrorCode.NOT_FOUND)
            return assets.upsert(
                uuid.uuid4().hex,
                owner_user_id=user_id,
                task_id=task.id,
                url=body.url,
                title=body.title,
                category=body.category,
                add_related_session_ids=body.add_related_session_ids,
            )

        return _asset_response(await asyncio.to_thread(attach))

    @router.delete(
        "/agent-tasks/{task_id}/assets/{asset_id}", response_model=AgentTaskAssetDeletedResponse
    )
    async def detach_asset(
        request: Request, task_id: str, asset_id: str
    ) -> AgentTaskAssetDeletedResponse:
        """Remove an asset from one of the caller's tasks."""
        user_id = require_user(request, auth_provider)
        deleted = await asyncio.to_thread(
            assets.delete, asset_id, owner_user_id=user_id, task_id=task_id
        )
        if not deleted:
            raise OmnigentError("task asset not found", code=ErrorCode.NOT_FOUND)
        return AgentTaskAssetDeletedResponse(id=asset_id, task_id=task_id)

    return router


def _asset_response(asset: TaskAsset) -> AgentTaskAssetResponse:
    return AgentTaskAssetResponse(
        id=asset.id,
        task_id=asset.task_id,
        category=asset.category,
        title=asset.title,
        url=asset.url,
        related_session_ids=asset.related_session_ids,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )
