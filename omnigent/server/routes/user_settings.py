"""Per-user settings the server applies on the caller's behalf (``/v1/settings``)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._session_create_validation import (
    validate_uploaded_bundle_host_workspace,
)
from omnigent.server.schemas import (
    GlobalHubSettingsRequest,
    GlobalHubSettingsResponse,
    SessionTitleInstructionsRequest,
    SessionTitleInstructionsResponse,
)
from omnigent.server.server_config import SESSION_TITLE_INSTRUCTIONS_MAX_CHARS
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore


def create_user_settings_router(
    store: UserSettingsStore,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the caller-scoped settings router.

    :param store: Where the settings persist.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
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

    @router.get("/settings/globalhub", response_model=GlobalHubSettingsResponse)
    async def get_globalhub_settings(request: Request) -> GlobalHubSettingsResponse:
        """Read where the caller's GlobalHub role sessions run."""
        user_id = require_user(request, auth_provider)
        placement = await asyncio.to_thread(store.get_globalhub_placement, user_id=user_id)
        return _globalhub_response(placement)

    @router.put("/settings/globalhub", response_model=GlobalHubSettingsResponse)
    async def save_globalhub_settings(
        request: Request, body: GlobalHubSettingsRequest
    ) -> GlobalHubSettingsResponse:
        """Set or clear where the caller's GlobalHub role sessions run.

        The host must be the caller's own and online, and the workspace an
        existing directory on it. The canonical path is stored.
        """
        user_id = require_user(request, auth_provider)
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
        return _globalhub_response(placement)

    return router


def _globalhub_response(placement: GlobalHubPlacement | None) -> GlobalHubSettingsResponse:
    if placement is None:
        return GlobalHubSettingsResponse(host_id=None, workspace=None)
    return GlobalHubSettingsResponse(host_id=placement.host_id, workspace=placement.workspace)
