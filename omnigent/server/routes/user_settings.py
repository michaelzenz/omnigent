"""Per-user settings the server applies on the caller's behalf (``/v1/settings``)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from omnigent.agent_tasks.custodian_sweep import next_sweep_at, require_sweep_timezone
from omnigent.db.utils import now_epoch
from omnigent.entities import CustodianSweep
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._session_create_validation import (
    validate_uploaded_bundle_host_workspace,
)
from omnigent.server.schemas import (
    CustodianSweepSettings,
    CustodianSweepSettingsResponse,
    GlobalHubSettingsRequest,
    GlobalHubSettingsResponse,
    SessionTitleInstructionsRequest,
    SessionTitleInstructionsResponse,
)
from omnigent.server.server_config import SESSION_TITLE_INSTRUCTIONS_MAX_CHARS
from omnigent.stores.custodian_sweep_store import CustodianSweepStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore


def create_user_settings_router(
    store: UserSettingsStore,
    auth_provider: AuthProvider | None = None,
    custodian_sweeps: CustodianSweepStore | None = None,
) -> APIRouter:
    """Build the caller-scoped settings router.

    :param store: Where the settings persist.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :param custodian_sweeps: When each user's custodian gets its daily cleanup
        message; ``None`` on a server without the GlobalHub board.
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
        sweep = (
            await asyncio.to_thread(custodian_sweeps.get, owner_user_id=user_id)
            if custodian_sweeps is not None
            else None
        )
        return _globalhub_response(placement, report_new_sessions, sweep)

    def require_custodian_sweeps(sweep: CustodianSweepSettings) -> CustodianSweepStore:
        if custodian_sweeps is None:
            raise OmnigentError(
                "The daily session cleanup is not available on this server.",
                code=ErrorCode.INVALID_INPUT,
            )
        require_sweep_timezone(sweep.timezone)
        return custodian_sweeps

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
        existing directory on it. The canonical path is stored. A daily
        session cleanup that is on runs next at its time after now.
        """
        user_id = require_user(request, auth_provider)
        sweep = body.custodian_sweep
        # Checked before any save, so a refused cleanup leaves the other settings unchanged.
        sweeps = require_custodian_sweeps(sweep) if sweep is not None else None
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
        await asyncio.to_thread(store.save_globalhub_placement, placement, user_id=user_id)
        if body.report_new_sessions is not None:
            await asyncio.to_thread(
                store.save_globalhub_report_new_sessions,
                body.report_new_sessions,
                user_id=user_id,
            )
        if sweeps is not None and sweep is not None:
            await asyncio.to_thread(
                sweeps.save,
                owner_user_id=user_id,
                local_time=sweep.time,
                timezone=sweep.timezone,
                next_run_at=(
                    next_sweep_at(sweep.time, sweep.timezone, after=now_epoch())
                    if sweep.enabled
                    else None
                ),
            )
        return await read_globalhub_settings(user_id)

    return router


def _globalhub_response(
    placement: GlobalHubPlacement | None,
    report_new_sessions: bool,
    sweep: CustodianSweep | None,
) -> GlobalHubSettingsResponse:
    return GlobalHubSettingsResponse(
        host_id=placement.host_id if placement else None,
        workspace=placement.workspace if placement else None,
        report_new_sessions=report_new_sessions,
        custodian_sweep=(
            CustodianSweepSettingsResponse(
                enabled=sweep.enabled,
                time=sweep.local_time,
                timezone=sweep.timezone,
                next_run_at=sweep.next_run_at,
            )
            if sweep is not None
            else None
        ),
    )
