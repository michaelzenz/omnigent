"""Per-user settings the server applies on the caller's behalf (``/v1/settings``)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request

from omnigent.agent_tasks.default_watchers import add_default_watchers, ensure_fallback_watcher
from omnigent.agent_tasks.role_schedules import (
    DEFAULT_CUSTODIAN_RRULE,
    require_role_schedule,
    start_default_custodian_schedule,
)
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_role_session
from omnigent.agent_tasks.roles import CUSTODIAN_ROLE_KEY, builtin_role
from omnigent.db.utils import now_epoch
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._session_create_validation import (
    validate_uploaded_bundle_host_workspace,
)
from omnigent.server.routes._sessions.helpers import _announce_session_added
from omnigent.server.schemas import (
    GlobalHubSettingsRequest,
    GlobalHubSettingsResponse,
    SessionTitleInstructionsRequest,
    SessionTitleInstructionsResponse,
)
from omnigent.server.server_config import SESSION_TITLE_INSTRUCTIONS_MAX_CHARS
from omnigent.stores.role_schedule_store import RoleScheduleStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore
from omnigent.stores.watcher_store import WatcherStore

_logger = logging.getLogger(__name__)


def create_user_settings_router(
    store: UserSettingsStore,
    auth_provider: AuthProvider | None = None,
    schedules: RoleScheduleStore | None = None,
    role_sessions: RoleSessionStores | None = None,
    watchers: WatcherStore | None = None,
) -> APIRouter:
    """Build the caller-scoped settings router.

    :param store: Where the settings persist.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :param schedules: When each user's schedulable roles run; ``None`` on a
        server without the GlobalHub board.
    :param role_sessions: Where GlobalHub role sessions are created; ``None``
        starts no custodian when the caller picks a placement.
    :param watchers: Where a new GlobalHub user's default watchers are created;
        ``None`` adds none.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    @router.get(
        "/settings/session-title-instructions",
        response_model=SessionTitleInstructionsResponse,
    )
    async def get_session_title_instructions(
        request: Request,
    ) -> SessionTitleInstructionsResponse:
        """Read the caller's own title instructions."""
        user_id = require_user(request, auth_provider)
        instructions = await asyncio.to_thread(
            store.get_session_title_instructions, user_id=user_id
        )
        return SessionTitleInstructionsResponse(
            instructions=instructions, max_chars=SESSION_TITLE_INSTRUCTIONS_MAX_CHARS
        )

    @router.put(
        "/settings/session-title-instructions",
        response_model=SessionTitleInstructionsResponse,
    )
    async def save_session_title_instructions(
        request: Request, body: SessionTitleInstructionsRequest
    ) -> SessionTitleInstructionsResponse:
        """Save or clear the caller's title instructions."""
        user_id = require_user(request, auth_provider)
        try:
            instructions = await asyncio.to_thread(
                store.save_session_title_instructions, body.instructions, user_id=user_id
            )
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        return SessionTitleInstructionsResponse(
            instructions=instructions, max_chars=SESSION_TITLE_INSTRUCTIONS_MAX_CHARS
        )

    async def read_globalhub_settings(user_id: str | None) -> GlobalHubSettingsResponse:
        placement, report_new_sessions = await asyncio.gather(
            asyncio.to_thread(store.get_globalhub_placement, user_id=user_id),
            asyncio.to_thread(store.get_globalhub_report_new_sessions, user_id=user_id),
        )
        return _globalhub_response(placement, report_new_sessions)

    @router.get("/settings/globalhub", response_model=GlobalHubSettingsResponse)
    async def get_globalhub_settings(request: Request) -> GlobalHubSettingsResponse:
        """Read where the caller's GlobalHub role sessions run, and what reports to the broker."""
        user_id = require_user(request, auth_provider)
        return await read_globalhub_settings(user_id)

    @router.put("/settings/globalhub", response_model=GlobalHubSettingsResponse)
    async def save_globalhub_settings(
        request: Request, body: GlobalHubSettingsRequest
    ) -> GlobalHubSettingsResponse:
        """Set or clear where the caller's GlobalHub role sessions run.

        The host must be the caller's own and online, and the workspace an
        existing directory on it. The canonical path is stored. A caller's
        first placement turns on the custodian's daily sweep, unless the caller
        has a custodian schedule already. It also adds the default GitHub,
        Slack and Jira watchers, unless the caller got them before.
        Each placement starts the custodian session there, unless it runs
        there already.
        """
        user_id = require_user(request, auth_provider)
        timezone = body.timezone or "UTC"
        if body.timezone is not None:
            require_role_schedule(DEFAULT_CUSTODIAN_RRULE, timezone)
        placement = None
        if body.host_id is not None and body.workspace is not None:
            # GlobalHub role agents declare no os_env boundary to check against.
            workspace = await validate_uploaded_bundle_host_workspace(
                user_id=user_id,
                host_id=body.host_id,
                workspace=body.workspace,
                spec_cwd=None,
                host_store=getattr(request.app.state, "host_store", None),
                host_registry=getattr(request.app.state, "host_registry", None),
            )
            placement = GlobalHubPlacement(host_id=body.host_id, workspace=workspace)
        earlier = await asyncio.to_thread(store.get_globalhub_placement, user_id=user_id)
        await asyncio.to_thread(store.save_globalhub_placement, placement, user_id=user_id)
        if schedules is not None and earlier is None and placement is not None:
            await asyncio.to_thread(
                start_default_custodian_schedule,
                schedules,
                owner_user_id=user_id,
                timezone=timezone,
                now=now_epoch(),
            )
            if watchers is not None:
                await asyncio.to_thread(
                    add_default_watchers,
                    watchers,
                    schedules,
                    store,
                    owner_user_id=user_id,
                    now=now_epoch(),
                )
        if schedules is not None and watchers is not None and placement is not None:
            await asyncio.to_thread(
                ensure_fallback_watcher,
                watchers,
                schedules,
                owner_user_id=user_id,
                now=now_epoch(),
            )
        if role_sessions is not None and placement is not None:
            await _start_custodian(role_sessions, user_id)
        if body.report_new_sessions is not None:
            await asyncio.to_thread(
                store.save_globalhub_report_new_sessions,
                body.report_new_sessions,
                user_id=user_id,
            )
        return await read_globalhub_settings(user_id)

    return router


async def _start_custodian(stores: RoleSessionStores, user_id: str | None) -> None:
    """Start the user's custodian session on the GlobalHub host, unless it runs there already."""
    custodian = builtin_role(CUSTODIAN_ROLE_KEY)
    assert custodian is not None
    try:
        session, created = await ensure_role_session(custodian, user_id=user_id, stores=stores)
    except OmnigentError:
        # The placement stays saved; the custodian starts with its first scheduled sweep.
        _logger.warning("Could not start the GlobalHub custodian session", exc_info=True)
        return
    if created:
        _announce_session_added(user_id, session.id)


def _globalhub_response(
    placement: GlobalHubPlacement | None, report_new_sessions: bool
) -> GlobalHubSettingsResponse:
    return GlobalHubSettingsResponse(
        host_id=placement.host_id if placement else None,
        workspace=placement.workspace if placement else None,
        report_new_sessions=report_new_sessions,
    )
