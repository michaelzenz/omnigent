"""Admin-managed tool preferences (deployment-global tool toggles)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.tool_preferences_store import ToolPreferencesStore
from omnigent.tools.catalog import ALL_TOOL_NAMES, get_catalog_response


class ToolPreferencesResponse(BaseModel):
    object: Literal["tool_preferences"]
    groups: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    disabled_tools: list[str]


class UpdateToolPreferencesRequest(BaseModel):
    disabled_tools: list[str] = Field(default_factory=list, max_length=500)


async def _require_admin(
    request: Request,
    auth_provider: AuthProvider | None,
    permission_store: PermissionStore | None,
) -> None:
    if auth_provider is None:
        return
    user_id = get_user_id(request, auth_provider)
    if permission_store is None:
        return
    if user_id is None:
        raise OmnigentError("Authentication required", code=ErrorCode.UNAUTHORIZED)
    if not await asyncio.to_thread(permission_store.is_admin, user_id):
        raise OmnigentError(
            "Admin privileges required to manage tool preferences",
            code=ErrorCode.FORBIDDEN,
        )


def create_tool_preferences_router(
    tool_preferences_store: ToolPreferencesStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build admin tool-preferences routes."""
    router = APIRouter()

    @router.get("/tool-preferences")
    async def get_tool_preferences(request: Request) -> dict[str, Any]:
        await _require_admin(request, auth_provider, permission_store)
        prefs = await asyncio.to_thread(tool_preferences_store.get)
        return get_catalog_response(prefs.disabled_tools)

    @router.put("/tool-preferences")
    async def update_tool_preferences(
        request: Request,
        body: UpdateToolPreferencesRequest,
    ) -> dict[str, Any]:
        await _require_admin(request, auth_provider, permission_store)
        unknown = [name for name in body.disabled_tools if name not in ALL_TOOL_NAMES]
        if unknown:
            raise OmnigentError(
                f"Unknown tool names in disabled_tools: {unknown}",
                code=ErrorCode.INVALID_INPUT,
            )
        user_id = get_user_id(request, auth_provider) if auth_provider else None
        prefs = await asyncio.to_thread(
            tool_preferences_store.update,
            disabled_tools=body.disabled_tools,
            updated_by=user_id,
        )
        return get_catalog_response(prefs.disabled_tools)

    return router
