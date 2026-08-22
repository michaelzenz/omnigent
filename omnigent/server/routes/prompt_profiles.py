"""CRUD routes for plain-text prompt profiles."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from omnigent.entities import PromptProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._origin import require_trusted_origin
from omnigent.server.schemas import (
    PromptProfileCreateRequest,
    PromptProfileObject,
    PromptProfilePatchRequest,
)
from omnigent.stores import PromptProfileStore


def _to_object(profile: PromptProfile) -> PromptProfileObject:
    return PromptProfileObject(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        instructions=profile.instructions,
        enabled=profile.enabled,
        visible=profile.visible,
        archived=profile.archived,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def create_prompt_profiles_router(
    store: PromptProfileStore,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Create the workspace-scoped prompt-profile router."""
    router = APIRouter()

    @router.get("/prompt-profiles")
    async def list_prompt_profiles(
        request: Request,
        enabled_only: bool = Query(default=False),
    ) -> list[PromptProfileObject]:
        require_user(request, auth_provider)
        profiles = await asyncio.to_thread(store.list, enabled_only=enabled_only)
        return [_to_object(profile) for profile in profiles]

    @router.post(
        "/prompt-profiles",
        status_code=201,
        dependencies=[Depends(require_trusted_origin)],
    )
    async def create_prompt_profile(
        request: Request,
        body: PromptProfileCreateRequest,
    ) -> PromptProfileObject:
        require_user(request, auth_provider)
        profile = await asyncio.to_thread(
            store.create,
            uuid.uuid4().hex,
            body.name,
            body.instructions,
            description=body.description,
            enabled=body.enabled,
        )
        return _to_object(profile)

    @router.patch(
        "/prompt-profiles/{profile_id}",
        dependencies=[Depends(require_trusted_origin)],
    )
    async def patch_prompt_profile(
        request: Request,
        profile_id: str,
        body: PromptProfilePatchRequest,
    ) -> PromptProfileObject:
        require_user(request, auth_provider)
        current = await asyncio.to_thread(store.get, profile_id)
        if current is not None and not current.visible:
            raise OmnigentError(
                "Internal prompt profiles must be edited through their owning surface",
                code=ErrorCode.CONFLICT,
            )
        fields = {
            name: getattr(body, name)
            for name in body.model_fields_set
            if name in {"name", "description", "instructions", "enabled"}
        }
        profile = await asyncio.to_thread(store.update, profile_id, **fields)
        if profile is None:
            raise OmnigentError(
                f"Prompt profile not found: {profile_id!r}",
                code=ErrorCode.NOT_FOUND,
            )
        return _to_object(profile)

    @router.delete(
        "/prompt-profiles/{profile_id}",
        status_code=204,
        dependencies=[Depends(require_trusted_origin)],
    )
    async def delete_prompt_profile(request: Request, profile_id: str) -> Response:
        require_user(request, auth_provider)
        current = await asyncio.to_thread(store.get, profile_id)
        if current is not None and not current.visible:
            raise OmnigentError(
                "Internal prompt profiles cannot be deleted directly",
                code=ErrorCode.CONFLICT,
            )
        profile = await asyncio.to_thread(store.archive, profile_id)
        if profile is None:
            raise OmnigentError(
                f"Prompt profile not found: {profile_id!r}",
                code=ErrorCode.NOT_FOUND,
            )
        return Response(status_code=204)

    return router
