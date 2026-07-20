"""API routes for ambient host poller state."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.ambient_codex import AmbientCodexTrack, HOST_AMBIENT_ID_HEADER
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores import ConversationStore
from omnigent.stores.host_store import HostStore


class AmbientCodexTrackResponse(BaseModel):
    """One Codex rollout track owned by a host poller."""

    session_id: str
    external_session_id: str
    thread_id: str
    byte_offset: int
    turn_id: str
    rollout_path: str
    connection_id: str | None = None
    workspace: str | None = None


class AmbientCursorTrackResponse(BaseModel):
    """One Cursor session track owned by a host poller."""

    session_id: str
    external_session_id: str
    session_key: str
    byte_offset: int
    turn_id: str
    source_path: str
    connection_id: str | None = None
    workspace: str | None = None


class AmbientCodexTracksResponse(BaseModel):
    """Hydration payload for a host's Codex ambient bridge."""

    tracks: list[AmbientCodexTrackResponse]


class AmbientCursorTracksResponse(BaseModel):
    """Hydration payload for a host's Cursor ambient bridge."""

    tracks: list[AmbientCursorTrackResponse]


def _track_to_response(track: AmbientCodexTrack) -> AmbientCodexTrackResponse:
    return AmbientCodexTrackResponse(
        session_id=track.session_id,
        external_session_id=track.external_session_id,
        thread_id=track.thread_id,
        byte_offset=track.byte_offset,
        turn_id=track.turn_id,
        rollout_path=track.rollout_path,
        connection_id=track.connection_id,
        workspace=track.workspace,
    )


def _cursor_track_to_response(track: AmbientCodexTrack) -> AmbientCursorTrackResponse:
    return AmbientCursorTrackResponse(
        session_id=track.session_id,
        external_session_id=track.external_session_id,
        session_key=track.thread_id,
        byte_offset=track.byte_offset,
        turn_id=track.turn_id,
        source_path=track.rollout_path,
        connection_id=track.connection_id,
        workspace=track.workspace,
    )


async def _require_host_access(
    request: Request,
    host_id: str,
    *,
    auth_provider: AuthProvider | None,
    host_store: HostStore,
) -> None:
    user_id = require_user(request, auth_provider)
    host = await asyncio.to_thread(host_store.get_host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    if user_id is not None and host.owner != user_id:
        raise HTTPException(status_code=403, detail="not your host")
    header_host_id = request.headers.get(HOST_AMBIENT_ID_HEADER)
    if header_host_id is not None and header_host_id.strip() != host_id:
        raise HTTPException(
            status_code=400,
            detail=f"{HOST_AMBIENT_ID_HEADER} must match the path host_id",
        )


def create_ambient_sync_router(
    conversation_store: ConversationStore,
    host_store: HostStore,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Create routes for server-persisted ambient poller state."""
    router = APIRouter()

    @router.get("/hosts/{host_id}/ambient/codex", response_model=AmbientCodexTracksResponse)
    async def list_codex_ambient_tracks(
        request: Request,
        host_id: str,
    ) -> dict[str, Any]:
        """Return Codex ambient tracks owned by ``host_id``."""
        await _require_host_access(
            request,
            host_id,
            auth_provider=auth_provider,
            host_store=host_store,
        )
        tracks = await asyncio.to_thread(
            conversation_store.list_ambient_codex_tracks,
            host_id,
        )
        return {
            "tracks": [_track_to_response(track).model_dump() for track in tracks],
        }

    @router.get(
        "/hosts/{host_id}/ambient/cursor-projects",
        response_model=AmbientCursorTracksResponse,
    )
    async def list_cursor_projects_ambient_tracks(
        request: Request,
        host_id: str,
    ) -> dict[str, Any]:
        """Return Cursor project ambient tracks owned by ``host_id``."""
        await _require_host_access(
            request,
            host_id,
            auth_provider=auth_provider,
            host_store=host_store,
        )
        tracks = await asyncio.to_thread(
            conversation_store.list_ambient_cursor_tracks,
            host_id,
            "cursor-projects",
        )
        return {
            "tracks": [_cursor_track_to_response(track).model_dump() for track in tracks],
        }

    return router
