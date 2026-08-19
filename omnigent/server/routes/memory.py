"""REST API for persistent categorized user memory."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Request

from omnigent.entities.memory import MemoryCategory
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.memory import DEFAULT_MEMORY_MAX_TOKENS
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    CreateMemoryCategoryRequest,
    ReorderMemoryCategoriesRequest,
    UpdateMemoryCategoryRequest,
    UpdateMemorySettingsRequest,
)
from omnigent.stores.memory_store import MemoryStore


def _category(category: MemoryCategory) -> dict[str, Any]:
    return {
        "id": category.id,
        "name": category.name,
        "display_order": category.display_order,
        "content": category.content,
        "token_count": category.token_count,
        "created_at": category.created_at,
        "updated_at": category.updated_at,
    }


def _response(categories: list[MemoryCategory], max_tokens: int) -> dict[str, Any]:
    used_tokens = sum(category.token_count for category in categories)
    return {
        "categories": [_category(category) for category in categories],
        "used_tokens": used_tokens,
        "max_tokens": max_tokens,
        "usage_percent": (used_tokens / max_tokens * 100) if max_tokens else 0.0,
        "over_limit": used_tokens > max_tokens,
    }


def create_memory_router(
    *,
    memory_store: MemoryStore,
    auth_provider: AuthProvider | None,
    max_tokens: int = DEFAULT_MEMORY_MAX_TOKENS,
) -> APIRouter:
    """Build the owner-scoped memory router."""
    router = APIRouter()

    async def current_max_tokens(user_id: str | None) -> int:
        return await asyncio.to_thread(
            memory_store.get_max_tokens,
            user_id=user_id,
            default=max_tokens,
        )

    @router.get("/memory")
    async def list_memory(request: Request) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        categories = await asyncio.to_thread(memory_store.list, user_id=user_id)
        return _response(categories, await current_max_tokens(user_id))

    @router.patch("/memory/settings")
    async def update_memory_settings(
        request: Request,
        body: UpdateMemorySettingsRequest,
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        effective_max = await asyncio.to_thread(
            memory_store.set_max_tokens,
            body.max_tokens,
            user_id=user_id,
        )
        categories = await asyncio.to_thread(memory_store.list, user_id=user_id)
        return _response(categories, effective_max)

    @router.post("/memory/categories")
    async def create_category(
        request: Request, body: CreateMemoryCategoryRequest
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        await asyncio.to_thread(memory_store.list, user_id=user_id)
        await asyncio.to_thread(
            memory_store.create,
            uuid.uuid4().hex,
            user_id=user_id,
            name=body.name,
            content=body.content,
            display_order=body.display_order,
        )
        categories = await asyncio.to_thread(memory_store.list, user_id=user_id)
        return _response(categories, await current_max_tokens(user_id))

    @router.patch("/memory/categories/{category_id}")
    async def update_category(
        request: Request, category_id: str, body: UpdateMemoryCategoryRequest
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        category = await asyncio.to_thread(
            memory_store.update,
            category_id,
            user_id=user_id,
            name=body.name,
            content=body.content,
            display_order=body.display_order,
        )
        if category is None:
            raise OmnigentError("Memory category not found", code=ErrorCode.NOT_FOUND)
        categories = await asyncio.to_thread(memory_store.list, user_id=user_id)
        return _response(categories, await current_max_tokens(user_id))

    @router.delete("/memory/categories/{category_id}")
    async def delete_category(request: Request, category_id: str) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        if not await asyncio.to_thread(memory_store.delete, category_id, user_id=user_id):
            raise OmnigentError("Memory category not found", code=ErrorCode.NOT_FOUND)
        categories = await asyncio.to_thread(memory_store.list, user_id=user_id)
        return _response(categories, await current_max_tokens(user_id))

    @router.put("/memory/order")
    async def reorder_categories(
        request: Request, body: ReorderMemoryCategoriesRequest
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        categories = await asyncio.to_thread(
            memory_store.reorder, body.ordered_ids, user_id=user_id
        )
        return _response(categories, await current_max_tokens(user_id))

    return router
