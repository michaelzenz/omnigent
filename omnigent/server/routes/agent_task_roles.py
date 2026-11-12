"""The caller's definitions of the GlobalHub roles (``/v1/agent-tasks/roles``)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from omnigent.agent_tasks.roles import (
    BUILTIN_ROLES,
    ROLE_OVERRIDE_FIELDS,
    BuiltinRole,
    RoleDefinition,
    builtin_role,
    role_definition,
)
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    GlobalHubRoleProfile,
    GlobalHubRoleProfileDetail,
    GlobalHubRoleProfileList,
    UpdateGlobalHubRoleProfileRequest,
)
from omnigent.stores.user_settings_store import UserSettingsStore


def create_agent_task_roles_router(
    store: UserSettingsStore,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the caller's GlobalHub role definitions.

    :param store: Where each user's role overrides persist.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    @router.get("/agent-tasks/roles/profiles", response_model=GlobalHubRoleProfileList)
    async def list_role_profiles(
        request: Request, kind: str | None = None
    ) -> GlobalHubRoleProfileList:
        """List the caller's role definitions, optionally of one kind, e.g. ``manager``."""
        user_id = require_user(request, auth_provider)
        roles = [role for role in BUILTIN_ROLES if kind is None or role.kind == kind]

        def load() -> list[RoleDefinition]:
            return [
                role_definition(
                    role, store.get_globalhub_role_overrides(role.key, user_id=user_id)
                )
                for role in roles
            ]

        definitions = await asyncio.to_thread(load)
        return GlobalHubRoleProfileList(data=[_profile(definition) for definition in definitions])

    @router.get("/agent-tasks/roles/{role}/profile", response_model=GlobalHubRoleProfileDetail)
    async def get_role_profile(request: Request, role: str) -> GlobalHubRoleProfileDetail:
        """Read one of the caller's role definitions, including its manual."""
        user_id = require_user(request, auth_provider)
        defined_role = _require_role(role)
        overrides = await asyncio.to_thread(
            store.get_globalhub_role_overrides, defined_role.key, user_id=user_id
        )
        return _profile_detail(role_definition(defined_role, overrides))

    @router.put("/agent-tasks/roles/{role}/profile", response_model=GlobalHubRoleProfileDetail)
    async def update_role_profile(
        request: Request, role: str, body: UpdateGlobalHubRoleProfileRequest
    ) -> GlobalHubRoleProfileDetail:
        """Override fields of the caller's role definition.

        Omitted fields keep their value; null or blank restores the field's default.
        """
        user_id = require_user(request, auth_provider)
        defined_role = _require_role(role)

        def update() -> RoleDefinition:
            overrides = store.get_globalhub_role_overrides(defined_role.key, user_id=user_id)
            for field in ROLE_OVERRIDE_FIELDS:
                if field not in body.model_fields_set:
                    continue
                value = getattr(body, field)
                value = value.strip() if value else ""
                if value:
                    overrides[field] = value
                else:
                    overrides.pop(field, None)
            store.save_globalhub_role_overrides(defined_role.key, overrides, user_id=user_id)
            return role_definition(defined_role, overrides)

        return _profile_detail(await asyncio.to_thread(update))

    return router


def _require_role(key: str) -> BuiltinRole:
    role = builtin_role(key)
    if role is None:
        raise OmnigentError(f"unknown GlobalHub role: {key}", code=ErrorCode.NOT_FOUND)
    return role


def _profile(definition: RoleDefinition) -> GlobalHubRoleProfile:
    return GlobalHubRoleProfile(
        role=definition.role.key,
        kind=definition.role.kind,
        title=definition.role.title,
        description=definition.description,
        model=definition.model,
        customized=list(definition.customized),
    )


def _profile_detail(definition: RoleDefinition) -> GlobalHubRoleProfileDetail:
    return GlobalHubRoleProfileDetail(
        **_profile(definition).model_dump(), manual=definition.manual
    )
