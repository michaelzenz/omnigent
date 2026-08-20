"""REST API for persistent categorized user memory."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from omnigent.entities.memory import MemoryCategory
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.memory import (
    DEFAULT_MEMORY_MAX_TOKENS,
    DEFAULT_MEMORY_PROVIDER,
    MEMORY_PROVIDER_GLOBAL_PATHS,
    MemoryProvider,
    count_memory_tokens,
)
from omnigent.server.auth import AuthProvider
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._host_filesystem import (
    HostFsError,
    HostFsUnavailableError,
    read_workspace_from_host,
)
from omnigent.server.schemas import (
    CreateMemoryCategoryRequest,
    ReorderMemoryCategoriesRequest,
    UpdateMemoryCategoryRequest,
    UpdateMemorySettingsRequest,
)
from omnigent.stores.host_store import HostStore, host_is_live
from omnigent.stores.memory_store import MemoryStore


class MemoryFileVariantWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class MemoryFileSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_sha256: str


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


def _response(
    categories: list[MemoryCategory],
    max_tokens: int,
    provider: MemoryProvider,
) -> dict[str, Any]:
    used_tokens = sum(category.token_count for category in categories)
    return {
        "categories": [_category(category) for category in categories],
        "used_tokens": used_tokens,
        "max_tokens": max_tokens,
        "provider": provider,
        "usage_percent": (used_tokens / max_tokens * 100) if max_tokens else 0.0,
        "over_limit": used_tokens > max_tokens,
    }


def create_memory_router(
    *,
    memory_store: MemoryStore,
    auth_provider: AuthProvider | None,
    max_tokens: int = DEFAULT_MEMORY_MAX_TOKENS,
    host_registry: HostRegistry | None = None,
    host_store: HostStore | None = None,
) -> APIRouter:
    """Build the owner-scoped memory router."""
    router = APIRouter()

    async def current_max_tokens(user_id: str | None) -> int:
        return await asyncio.to_thread(
            memory_store.get_max_tokens,
            user_id=user_id,
            default=max_tokens,
        )

    async def current_provider(user_id: str | None) -> MemoryProvider:
        return await asyncio.to_thread(
            memory_store.get_provider,
            user_id=user_id,
            default=DEFAULT_MEMORY_PROVIDER,
        )

    def file_provider(raw: str) -> MemoryProvider:
        if raw not in {"claude", "agents"}:
            raise HTTPException(status_code=404, detail="Memory provider not found.")
        return cast(MemoryProvider, raw)

    async def host_memory_request(
        host_id: str,
        op: str,
        *,
        provider: MemoryProvider,
        workspace: str = "",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if host_registry is None:
            raise HTTPException(status_code=503, detail="Host registry is unavailable.")
        connection = host_registry.get(host_id)
        if connection is None:
            raise HTTPException(status_code=409, detail=f"Host {host_id!r} is offline.")
        try:
            payload = await read_workspace_from_host(
                host_registry=host_registry,
                host_conn=connection,
                op=op,
                workspace=workspace,
                session_id="",
                params={"provider": provider, **(params or {})},
            )
        except HostFsError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        except HostFsUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if op in {"memory.file.read", "memory.file.write"}:
            host_registry.record_memory_file(
                host_id,
                payload,
                workspace_id=connection.workspace_id,
            )
        return payload

    async def memory_file_inventory(
        user_id: str | None,
        provider: MemoryProvider,
    ) -> dict[str, Any]:
        if host_store is None or host_registry is None:
            return {
                "provider": provider,
                "rel_home_path": MEMORY_PROVIDER_GLOBAL_PATHS[provider],
                "variants": [],
                "hosts": [],
            }
        hosts = await asyncio.to_thread(host_store.list_hosts, user_id or "local")
        rows: list[dict[str, Any]] = []
        for host in hosts:
            online = host_is_live(host) and host_registry.get(host.host_id) is not None
            error: str | None = None
            value: dict[str, Any] | None = None
            if online:
                try:
                    value = await host_memory_request(
                        host.host_id,
                        "memory.file.read",
                        provider=provider,
                    )
                except HTTPException as exc:
                    error = exc.detail
            if value is None:
                connection = host_registry.get(host.host_id)
                cached = host_registry.memory_file(
                    host.host_id,
                    provider,
                    workspace_id=connection.workspace_id if connection is not None else None,
                )
                value = dict(cached) if cached is not None else None
            rows.append(
                {
                    "host_id": host.host_id,
                    "host_name": host.name,
                    "online": online,
                    "status": (
                        "present"
                        if value is not None and value.get("exists") is True
                        else "missing"
                        if value is not None
                        else "unknown"
                    ),
                    "content_sha256": value.get("content_sha256") if value is not None else None,
                    "error": error,
                    "_content": value.get("content", "") if value is not None else "",
                }
            )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        contents: dict[str, str] = {}
        for row in rows:
            content_hash = row.get("content_sha256")
            if not isinstance(content_hash, str):
                if row["status"] == "missing":
                    grouped["missing"].append(row)
                    contents["missing"] = ""
                continue
            grouped[content_hash].append(row)
            contents[content_hash] = str(row["_content"])
        variants = [
            {
                "content_sha256": content_hash,
                "content": contents[content_hash],
                "token_count": count_memory_tokens(contents[content_hash]),
                "active_count": sum(bool(row["online"]) for row in grouped_rows),
                "hosts": [
                    {key: value for key, value in row.items() if key != "_content"}
                    for row in sorted(
                        grouped_rows,
                        key=lambda item: (not item["online"], item["host_name"]),
                    )
                ],
            }
            for content_hash, grouped_rows in grouped.items()
        ]
        variants.sort(
            key=lambda item: (
                -item["active_count"],
                item["content_sha256"],
            )
        )
        return {
            "provider": provider,
            "rel_home_path": MEMORY_PROVIDER_GLOBAL_PATHS[provider],
            "variants": variants,
            "hosts": [
                {key: value for key, value in row.items() if key != "_content"} for row in rows
            ],
        }

    @router.get("/memory")
    async def list_memory(request: Request) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        categories = await asyncio.to_thread(memory_store.list, user_id=user_id)
        return _response(
            categories,
            await current_max_tokens(user_id),
            await current_provider(user_id),
        )

    @router.patch("/memory/settings")
    async def update_memory_settings(
        request: Request,
        body: UpdateMemorySettingsRequest,
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        if body.max_tokens is None and body.provider is None:
            raise OmnigentError(
                "At least one memory setting is required",
                code=ErrorCode.INVALID_INPUT,
            )
        effective_max = (
            await asyncio.to_thread(
                memory_store.set_max_tokens,
                body.max_tokens,
                user_id=user_id,
            )
            if body.max_tokens is not None
            else await current_max_tokens(user_id)
        )
        effective_provider = (
            await asyncio.to_thread(
                memory_store.set_provider,
                body.provider,
                user_id=user_id,
                default_max_tokens=max_tokens,
            )
            if body.provider is not None
            else await current_provider(user_id)
        )
        categories = await asyncio.to_thread(memory_store.list, user_id=user_id)
        return _response(categories, effective_max, effective_provider)

    @router.get("/memory/files/{provider}")
    async def list_memory_file_variants(
        request: Request,
        provider: str,
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        return await memory_file_inventory(user_id, file_provider(provider))

    @router.patch("/memory/files/{provider}/variants/{content_sha256}")
    async def update_memory_file_variant(
        request: Request,
        provider: str,
        content_sha256: str,
        body: MemoryFileVariantWriteRequest,
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        resolved_provider = file_provider(provider)
        inventory = await memory_file_inventory(user_id, resolved_provider)
        variant = next(
            (item for item in inventory["variants"] if item["content_sha256"] == content_sha256),
            None,
        )
        if variant is None:
            raise HTTPException(status_code=409, detail="Memory file variant is stale.")
        writable_hosts = [host for host in variant["hosts"] if host["online"]]
        if not writable_hosts:
            raise HTTPException(status_code=409, detail="No host in this variant is online.")
        for host in writable_hosts:
            await host_memory_request(
                host["host_id"],
                "memory.file.write",
                provider=resolved_provider,
                params={
                    "content": body.content,
                    "expected_sha256": None if content_sha256 == "missing" else content_sha256,
                },
            )
        return await memory_file_inventory(user_id, resolved_provider)

    @router.post("/memory/files/{provider}/sync")
    async def sync_memory_file_variant(
        request: Request,
        provider: str,
        body: MemoryFileSyncRequest,
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        resolved_provider = file_provider(provider)
        inventory = await memory_file_inventory(user_id, resolved_provider)
        source = next(
            (
                item
                for item in inventory["variants"]
                if item["content_sha256"] == body.source_sha256
            ),
            None,
        )
        if source is None:
            raise HTTPException(status_code=409, detail="Memory file variant is stale.")
        if body.source_sha256 == "missing":
            raise HTTPException(status_code=400, detail="Choose a populated variant to sync.")
        sync_results: list[dict[str, str]] = []
        for host in inventory["hosts"]:
            if not host["online"]:
                sync_results.append({"host_id": host["host_id"], "status": "offline"})
                continue
            if host["content_sha256"] == body.source_sha256:
                sync_results.append({"host_id": host["host_id"], "status": "unchanged"})
                continue
            await host_memory_request(
                host["host_id"],
                "memory.file.write",
                provider=resolved_provider,
                params={
                    "content": source["content"],
                    "expected_sha256": host["content_sha256"],
                },
            )
            sync_results.append({"host_id": host["host_id"], "status": "updated"})
        refreshed = await memory_file_inventory(user_id, resolved_provider)
        refreshed["sync_results"] = sync_results
        return refreshed

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
        return _response(
            categories,
            await current_max_tokens(user_id),
            await current_provider(user_id),
        )

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
        return _response(
            categories,
            await current_max_tokens(user_id),
            await current_provider(user_id),
        )

    @router.delete("/memory/categories/{category_id}")
    async def delete_category(request: Request, category_id: str) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        if not await asyncio.to_thread(memory_store.delete, category_id, user_id=user_id):
            raise OmnigentError("Memory category not found", code=ErrorCode.NOT_FOUND)
        categories = await asyncio.to_thread(memory_store.list, user_id=user_id)
        return _response(
            categories,
            await current_max_tokens(user_id),
            await current_provider(user_id),
        )

    @router.put("/memory/order")
    async def reorder_categories(
        request: Request, body: ReorderMemoryCategoriesRequest
    ) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        categories = await asyncio.to_thread(
            memory_store.reorder, body.ordered_ids, user_id=user_id
        )
        return _response(
            categories,
            await current_max_tokens(user_id),
            await current_provider(user_id),
        )

    return router
