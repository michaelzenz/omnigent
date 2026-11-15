"""The caller's GlobalHub watchers (``/v1/agent-tasks/watchers``).

Include this router before the tasks router: ``/agent-tasks/{task_id}`` would
otherwise match ``/agent-tasks/watchers``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Request

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.role_schedules import (
    DEFAULT_WATCHER_RRULE,
    require_role_schedule,
    save_role_schedule,
)
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_watcher_session
from omnigent.agent_tasks.roles import WATCHER_ROLE
from omnigent.db.utils import now_epoch
from omnigent.entities import RoleSchedule, Watcher
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.models.model_override import validate_model_override
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._sessions.helpers import _announce_session_added
from omnigent.server.routes.agent_task_roles import role_schedule_response
from omnigent.server.schemas import (
    CreateGlobalHubWatcherRequest,
    GlobalHubRoleSchedule,
    GlobalHubWatcher,
    GlobalHubWatcherDefaults,
    GlobalHubWatcherDeleted,
    GlobalHubWatcherDetail,
    GlobalHubWatcherList,
    GlobalHubWatcherSessionResponse,
    UpdateGlobalHubWatcherRequest,
)
from omnigent.stores.watcher_store import UNSET

# A watcher created without a schedule runs on the default rule; minute
# intervals do not depend on the timezone.
_DEFAULT_SCHEDULE = GlobalHubRoleSchedule(
    enabled=True, rrule=DEFAULT_WATCHER_RRULE, timezone="UTC"
)


def create_agent_task_watchers_router(
    board: BoardStores,
    sessions: RoleSessionStores,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for the caller's GlobalHub watchers.

    :param board: Holds the watchers and their schedules.
    :param sessions: Where watcher sessions are created.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    watchers = board.watchers
    schedules = board.role_schedules

    def save_schedule(
        watcher: Watcher, schedule: GlobalHubRoleSchedule, user_id: str | None
    ) -> None:
        save_role_schedule(
            schedules,
            watcher.role_key,
            owner_user_id=user_id,
            rrule=schedule.rrule.strip(),
            timezone=schedule.timezone,
            enabled=schedule.enabled,
            now=now_epoch(),
        )

    def require_watcher(watcher_id: str, user_id: str | None) -> Watcher:
        watcher = watchers.get(watcher_id, owner_user_id=user_id)
        if watcher is None:
            raise OmnigentError("watcher not found", code=ErrorCode.NOT_FOUND)
        return watcher

    @router.get("/agent-tasks/watchers", response_model=GlobalHubWatcherList)
    async def list_watchers(request: Request) -> GlobalHubWatcherList:
        """List the caller's watchers in creation order, each with its schedule."""
        user_id = require_user(request, auth_provider)

        def load() -> list[tuple[Watcher, RoleSchedule | None]]:
            return [
                (watcher, schedules.get(watcher.role_key, owner_user_id=user_id))
                for watcher in watchers.list(owner_user_id=user_id)
            ]

        listed = await asyncio.to_thread(load)
        return GlobalHubWatcherList(
            data=[_watcher(watcher, schedule) for watcher, schedule in listed]
        )

    @router.get("/agent-tasks/watchers/defaults", response_model=GlobalHubWatcherDefaults)
    async def get_watcher_defaults(request: Request) -> GlobalHubWatcherDefaults:
        """Return what a new watcher starts from: the manual template and a schedule rule."""
        require_user(request, auth_provider)
        return GlobalHubWatcherDefaults(
            manual=WATCHER_ROLE.default_manual(), rrule=DEFAULT_WATCHER_RRULE
        )

    @router.post("/agent-tasks/watchers", response_model=GlobalHubWatcherDetail)
    async def create_watcher(
        request: Request, body: CreateGlobalHubWatcherRequest
    ) -> GlobalHubWatcherDetail:
        """Create a watcher and its schedule; its session starts with its first run.

        The model and the schedule are checked before anything is saved.
        """
        user_id = require_user(request, auth_provider)
        _check_model(body.model)
        schedule = body.schedule or _DEFAULT_SCHEDULE
        require_role_schedule(schedule.rrule.strip(), schedule.timezone)

        def create() -> tuple[Watcher, RoleSchedule | None]:
            watcher = watchers.create(
                uuid.uuid4().hex,
                owner_user_id=user_id,
                name=body.name,
                description=body.description.strip(),
                manual=_text_or_none(body.manual),
                model=_text_or_none(body.model),
            )
            save_schedule(watcher, schedule, user_id)
            return watcher, schedules.get(watcher.role_key, owner_user_id=user_id)

        watcher, saved = await asyncio.to_thread(create)
        return _watcher_detail(watcher, saved)

    @router.get("/agent-tasks/watchers/{watcher_id}", response_model=GlobalHubWatcherDetail)
    async def get_watcher(request: Request, watcher_id: str) -> GlobalHubWatcherDetail:
        """Read one of the caller's watchers, including its manual and schedule."""
        user_id = require_user(request, auth_provider)

        def load() -> tuple[Watcher, RoleSchedule | None]:
            watcher = require_watcher(watcher_id, user_id)
            return watcher, schedules.get(watcher.role_key, owner_user_id=user_id)

        return _watcher_detail(*await asyncio.to_thread(load))

    @router.patch("/agent-tasks/watchers/{watcher_id}", response_model=GlobalHubWatcherDetail)
    async def update_watcher(
        request: Request, watcher_id: str, body: UpdateGlobalHubWatcherRequest
    ) -> GlobalHubWatcherDetail:
        """Change fields of one of the caller's watchers, or its schedule.

        Omitted fields keep their value. The model and the schedule are checked
        before anything is saved, so a refused request changes nothing. A new
        model applies to the next session; the web switches the running one.
        """
        user_id = require_user(request, auth_provider)
        fields = body.model_fields_set
        if "model" in fields:
            _check_model(body.model)
        if "schedule" in fields and body.schedule is not None:
            require_role_schedule(body.schedule.rrule.strip(), body.schedule.timezone)

        def update() -> tuple[Watcher, RoleSchedule | None]:
            watcher = require_watcher(watcher_id, user_id)
            updated = watchers.update(
                watcher.id,
                owner_user_id=user_id,
                name=body.name if body.name is not None else UNSET,
                description=(
                    (body.description or "").strip() if "description" in fields else UNSET
                ),
                manual=_text_or_none(body.manual) if "manual" in fields else UNSET,
                model=_text_or_none(body.model) if "model" in fields else UNSET,
            )
            if "schedule" in fields:
                if body.schedule is None:
                    schedules.delete(watcher.role_key, owner_user_id=user_id)
                else:
                    save_schedule(watcher, body.schedule, user_id)
            return updated or watcher, schedules.get(watcher.role_key, owner_user_id=user_id)

        return _watcher_detail(*await asyncio.to_thread(update))

    @router.delete("/agent-tasks/watchers/{watcher_id}", response_model=GlobalHubWatcherDeleted)
    async def delete_watcher(request: Request, watcher_id: str) -> GlobalHubWatcherDeleted:
        """Delete one of the caller's watchers and its schedule.

        The watcher's session is left as an ordinary session, and a run already
        queued for it is dropped when its turn would start.
        """
        user_id = require_user(request, auth_provider)
        deleted = await asyncio.to_thread(watchers.delete, watcher_id, owner_user_id=user_id)
        if deleted is None:
            raise OmnigentError("watcher not found", code=ErrorCode.NOT_FOUND)
        return GlobalHubWatcherDeleted(id=deleted.id, conversation_id=deleted.conversation_id)

    @router.post(
        "/agent-tasks/watchers/{watcher_id}/session",
        response_model=GlobalHubWatcherSessionResponse,
    )
    async def ensure_caller_watcher_session(
        request: Request, watcher_id: str
    ) -> GlobalHubWatcherSessionResponse:
        """Return a watcher's session on the caller's GlobalHub host, creating it when needed."""
        user_id = require_user(request, auth_provider)
        session, created = await ensure_watcher_session(
            watcher_id, user_id=user_id, stores=sessions, watchers=watchers
        )
        if created:
            _announce_session_added(user_id, session.id)
        return GlobalHubWatcherSessionResponse(
            watcher_id=watcher_id, session_id=session.id, created=created
        )

    return router


def _check_model(model: str | None) -> None:
    # The watcher's session starts with this model, so it gets a session override's check.
    if model and model.strip():
        try:
            validate_model_override(model)
        except ValueError as exc:
            raise OmnigentError(f"invalid model: {exc}", code=ErrorCode.INVALID_INPUT) from exc


def _text_or_none(value: str | None) -> str | None:
    stripped = value.strip() if value else ""
    return stripped or None


def _watcher(watcher: Watcher, schedule: RoleSchedule | None) -> GlobalHubWatcher:
    customized: list[Literal["manual"]] = ["manual"] if watcher.manual is not None else []
    return GlobalHubWatcher(
        id=watcher.id,
        name=watcher.name,
        description=watcher.description,
        model=watcher.model,
        customized=customized,
        schedule=role_schedule_response(schedule),
        session_id=watcher.conversation_id,
        created_at=watcher.created_at,
        updated_at=watcher.updated_at,
    )


def _watcher_detail(watcher: Watcher, schedule: RoleSchedule | None) -> GlobalHubWatcherDetail:
    default_manual = WATCHER_ROLE.default_manual()
    return GlobalHubWatcherDetail(
        **_watcher(watcher, schedule).model_dump(),
        manual=watcher.manual or default_manual,
        default_manual=default_manual,
    )
