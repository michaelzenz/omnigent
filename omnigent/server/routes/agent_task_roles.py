"""The caller's GlobalHub roles and role sessions (``/v1/agent-tasks/roles``)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from omnigent.agent_tasks.role_schedules import next_run_at, require_role_schedule
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_role_session
from omnigent.agent_tasks.roles import (
    BUILTIN_ROLES,
    ROLE_OVERRIDE_FIELDS,
    BuiltinRole,
    RoleDefinition,
    builtin_role,
    role_definition,
)
from omnigent.db.utils import now_epoch
from omnigent.entities import RoleSchedule
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.models.model_override import validate_model_override
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._sessions.helpers import _announce_session_added
from omnigent.server.schemas import (
    GlobalHubRoleProfile,
    GlobalHubRoleProfileDetail,
    GlobalHubRoleProfileList,
    GlobalHubRoleSchedule,
    GlobalHubRoleScheduleResponse,
    GlobalHubRoleSessionResponse,
    UpdateGlobalHubRoleProfileRequest,
)
from omnigent.stores.role_schedule_store import RoleScheduleStore


def create_agent_task_roles_router(
    stores: RoleSessionStores,
    auth_provider: AuthProvider | None = None,
    schedules: RoleScheduleStore | None = None,
) -> APIRouter:
    """Build the router for the caller's GlobalHub roles and role sessions.

    :param stores: Where role overrides and role sessions persist.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :param schedules: When each user's schedulable roles run; ``None`` on a
        server without the GlobalHub board, where no role has a schedule.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    store = stores.user_settings_store

    def schedulable(role: BuiltinRole) -> bool:
        return schedules is not None and role.schedulable

    def read_schedule(role: BuiltinRole, user_id: str | None) -> RoleSchedule | None:
        if schedules is None or not role.schedulable:
            return None
        return schedules.get(role.key, owner_user_id=user_id)

    def save_schedule(
        role: BuiltinRole, schedule: GlobalHubRoleSchedule | None, user_id: str | None
    ) -> None:
        assert schedules is not None
        if schedule is None:
            schedules.delete(role.key, owner_user_id=user_id)
            return
        now = now_epoch()
        saved = schedules.get(role.key, owner_user_id=user_id)
        rrule = schedule.rrule.strip()
        upcoming = (
            next_run_at(
                rrule,
                schedule.timezone,
                after=now,
                anchor=saved.created_at if saved is not None else now,
            )
            if schedule.enabled
            else None
        )
        schedules.save(
            role.key,
            owner_user_id=user_id,
            rrule=rrule,
            timezone=schedule.timezone,
            next_run_at=upcoming,
        )

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
        """Read one of the caller's role definitions, including its manual and schedule."""
        user_id = require_user(request, auth_provider)
        defined_role = _require_role(role)

        def load() -> tuple[RoleDefinition, RoleSchedule | None]:
            overrides = store.get_globalhub_role_overrides(defined_role.key, user_id=user_id)
            return role_definition(defined_role, overrides), read_schedule(defined_role, user_id)

        definition, schedule = await asyncio.to_thread(load)
        return _profile_detail(definition, schedule, schedulable=schedulable(defined_role))

    @router.put("/agent-tasks/roles/{role}/profile", response_model=GlobalHubRoleProfileDetail)
    async def update_role_profile(
        request: Request, role: str, body: UpdateGlobalHubRoleProfileRequest
    ) -> GlobalHubRoleProfileDetail:
        """Override fields of the caller's role definition, or set its schedule.

        Omitted fields keep their value; null or blank restores the field's default.
        A model must pass the same check as a session's model override, because
        the role's sessions start with it. A schedule must repeat without an end,
        at least 10 minutes apart. Both checks come before any save, so a refused
        request changes nothing.
        """
        user_id = require_user(request, auth_provider)
        defined_role = _require_role(role)
        if body.model and body.model.strip():
            try:
                validate_model_override(body.model)
            except ValueError as exc:
                raise OmnigentError(f"invalid model: {exc}", code=ErrorCode.INVALID_INPUT) from exc
        schedule_set = "schedule" in body.model_fields_set
        if schedule_set:
            if not schedulable(defined_role):
                raise OmnigentError(
                    f"GlobalHub role {defined_role.key!r} does not run on a schedule",
                    code=ErrorCode.INVALID_INPUT,
                )
            if body.schedule is not None:
                require_role_schedule(body.schedule.rrule.strip(), body.schedule.timezone)

        def update() -> tuple[RoleDefinition, RoleSchedule | None]:
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
            if schedule_set:
                save_schedule(defined_role, body.schedule, user_id)
            return role_definition(defined_role, overrides), read_schedule(defined_role, user_id)

        definition, schedule = await asyncio.to_thread(update)
        return _profile_detail(definition, schedule, schedulable=schedulable(defined_role))

    @router.post("/agent-tasks/roles/{role}/session", response_model=GlobalHubRoleSessionResponse)
    async def ensure_caller_role_session(
        request: Request, role: str
    ) -> GlobalHubRoleSessionResponse:
        """Return the caller's broker or custodian session, creating it when needed.

        The session runs on the caller's GlobalHub host and workspace. Each user
        has one broker and one custodian session, but may have several managers,
        each with its own session that the user's broker creates.
        """
        user_id = require_user(request, auth_provider)
        defined_role = _require_role(role)
        if not defined_role.singleton:
            raise OmnigentError(
                f"GlobalHub role {defined_role.key!r} has a session per manager, not one per user",
                code=ErrorCode.INVALID_INPUT,
            )
        session, created = await ensure_role_session(defined_role, user_id=user_id, stores=stores)
        if created:
            _announce_session_added(user_id, session.id)
        return GlobalHubRoleSessionResponse(
            role=defined_role.key, session_id=session.id, created=created
        )

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


def _profile_detail(
    definition: RoleDefinition, schedule: RoleSchedule | None, *, schedulable: bool
) -> GlobalHubRoleProfileDetail:
    return GlobalHubRoleProfileDetail(
        **_profile(definition).model_dump(),
        manual=definition.manual,
        default_description=definition.role.description,
        default_manual=definition.role.default_manual(),
        schedulable=schedulable,
        schedule=(
            GlobalHubRoleScheduleResponse(
                enabled=schedule.enabled,
                rrule=schedule.rrule,
                timezone=schedule.timezone,
                next_run_at=schedule.next_run_at,
            )
            if schedule is not None
            else None
        ),
    )
