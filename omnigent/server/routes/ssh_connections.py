"""SSH connection routes for the settings UI and host daemon."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.ssh_connections_store import (
    SshConnectionProfile,
    SshSettings,
    new_ssh_connection_id,
    profile_to_api_dict,
    read_ssh_connections,
    read_ssh_settings,
    validate_package_index_url,
    validate_ssh_alias,
    validate_ssh_connection_id,
    write_ssh_connections,
    write_ssh_settings,
)
from omnigent.ssh_probe import SshProbeRequest, probe_ssh
from omnigent.stores.permission_store import PermissionStore


class SshConnectionBody(BaseModel):
    """One SSH connection profile in API requests."""

    id: str | None = None
    label: str = Field(..., min_length=1, max_length=128)
    alias: str = Field(..., min_length=1, max_length=128)


class SshConnectionsPutRequest(BaseModel):
    """Body for ``PUT /v1/ssh/connections``."""

    connections: list[SshConnectionBody]
    package_index_url: str | None = None


def _normalize_package_index_url(raw: str | None) -> str | None:
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    error = validate_package_index_url(trimmed)
    if error is not None:
        raise HTTPException(status_code=400, detail=error)
    return trimmed


def _connections_response(
    profiles: list[SshConnectionProfile],
    *,
    snapshots: dict[str, object],
    host_store: object | None,
    online_by_host_id: dict[str, bool] | None = None,
) -> dict[str, object]:
    settings = read_ssh_settings()
    result: list[dict[str, object]] = []
    for profile in profiles:
        item = profile_to_api_dict(profile)
        state = snapshots.get(profile.id)
        online = False
        if state is not None and host_store is not None:
            if online_by_host_id is not None:
                online = online_by_host_id.get(state.host_id, False)  # type: ignore[union-attr]
            else:
                online = host_store.is_online(state.host_id)  # type: ignore[union-attr]
        if state is not None:
            item.update(
                {
                    "host_id": state.host_id,  # type: ignore[union-attr]
                    "lifecycle": state.desired_state,  # type: ignore[union-attr]
                    "phase": state.phase,  # type: ignore[union-attr]
                    "last_error": state.last_error,  # type: ignore[union-attr]
                    "attempt": state.attempt,  # type: ignore[union-attr]
                    "next_retry_at": (
                        datetime.fromtimestamp(state.next_attempt_at, UTC).isoformat()  # type: ignore[union-attr]
                        if state.next_attempt_at is not None  # type: ignore[union-attr]
                        else None
                    ),
                    "updated_at": datetime.fromtimestamp(state.updated_at, UTC).isoformat(),  # type: ignore[union-attr]
                    "status": "online" if online else "offline",
                }
            )
        else:
            item.update(
                {
                    "host_id": None,
                    "lifecycle": "connected",
                    "phase": "queued",
                    "last_error": None,
                    "attempt": 0,
                    "next_retry_at": None,
                    "updated_at": profile.created_at,
                    "status": "offline",
                }
            )
        result.append(item)
    return {
        "connections": result,
        "package_index_url": settings.package_index_url,
    }


async def _build_connections_payload(
    profiles: list[SshConnectionProfile],
    request: Request,
) -> dict[str, object]:
    manager = getattr(request.app.state, "ssh_host_manager", None)
    snapshots = await asyncio.to_thread(manager.snapshot) if manager is not None else {}
    host_store = getattr(request.app.state, "host_store", None)
    online_by_host_id: dict[str, bool] = {}
    if host_store is not None:
        for state in snapshots.values():
            host_id = state.host_id  # type: ignore[union-attr]
            online_by_host_id[host_id] = await asyncio.to_thread(host_store.is_online, host_id)
    return _connections_response(
        profiles,
        snapshots=snapshots,
        host_store=host_store,
        online_by_host_id=online_by_host_id,
    )


class SshTestRequest(BaseModel):
    """Body for ``POST /v1/ssh/test``."""

    alias: str = Field(..., min_length=1, max_length=128)


class SshTestResponse(BaseModel):
    """Result of an SSH connectivity probe."""

    ok: bool
    message: str
    latency_ms: int | None = None


def _parse_profiles(
    body: SshConnectionsPutRequest,
    *,
    owner: str,
) -> list[SshConnectionProfile]:
    existing = {profile.id: profile for profile in read_ssh_connections()}
    profiles: list[SshConnectionProfile] = []
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()
    for entry in body.connections:
        alias_error = validate_ssh_alias(entry.alias)
        if alias_error is not None:
            raise HTTPException(status_code=400, detail=alias_error)
        label = entry.label.strip()
        if not label:
            raise HTTPException(status_code=400, detail="Label is required")
        profile_id = entry.id.strip() if entry.id else new_ssh_connection_id()
        id_error = validate_ssh_connection_id(profile_id)
        if id_error is not None:
            raise HTTPException(status_code=400, detail=id_error)
        if profile_id in seen_ids:
            raise HTTPException(status_code=400, detail=f"Duplicate connection id: {profile_id}")
        seen_ids.add(profile_id)
        alias = entry.alias.strip()
        if alias in seen_aliases:
            raise HTTPException(status_code=400, detail=f"Duplicate SSH alias: {alias}")
        seen_aliases.add(alias)
        prior = existing.get(profile_id)
        if prior is not None and prior.alias != alias:
            raise HTTPException(
                status_code=400,
                detail="SSH aliases cannot be edited; remove and re-add the connection",
            )
        created_at = prior.created_at if prior is not None else datetime.now(UTC).isoformat()
        profiles.append(
            SshConnectionProfile(
                id=profile_id,
                label=label,
                alias=alias,
                created_at=created_at,
                owner=(
                    prior.owner
                    if prior is not None and prior.owner not in (None, "local")
                    else owner
                ),
            )
        )
    return profiles


def create_ssh_connections_router(
    *,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the router for SSH settings helpers."""
    router = APIRouter()

    async def _require_admin(request: Request) -> str | None:
        user_id = require_user(request, auth_provider)
        if permission_store is not None and user_id is not None:
            is_admin = await asyncio.to_thread(permission_store.is_admin, user_id)
            if not is_admin:
                raise HTTPException(status_code=403, detail="Admin privileges required")
        return user_id

    @router.get("/ssh/connections")
    async def list_ssh_connections(request: Request) -> dict[str, object]:
        """List SSH connection profiles stored on this host."""
        require_user(request, auth_provider)
        profiles = read_ssh_connections()
        return await _build_connections_payload(profiles, request)

    @router.put("/ssh/connections")
    async def put_ssh_connections(
        body: SshConnectionsPutRequest,
        request: Request,
    ) -> dict[str, object]:
        """Replace SSH connection profiles stored on this host."""
        user_id = await _require_admin(request)
        profiles = _parse_profiles(body, owner=user_id or "local")
        prior_index_url = read_ssh_settings().package_index_url
        package_index_url = _normalize_package_index_url(body.package_index_url)
        write_ssh_connections(profiles)
        write_ssh_settings(SshSettings(package_index_url=package_index_url))
        manager = getattr(request.app.state, "ssh_host_manager", None)
        if manager is not None:
            await asyncio.to_thread(manager.sync_profiles, profiles, owner=user_id or "local")
            if package_index_url != prior_index_url:
                await asyncio.to_thread(manager.requeue_connected_installations)
        return await _build_connections_payload(profiles, request)

    @router.post("/ssh/connections/{connection_id}/retry")
    async def retry_ssh_connection(connection_id: str, request: Request) -> dict[str, bool]:
        """Queue an immediate reconciliation attempt."""
        await _require_admin(request)
        manager = getattr(request.app.state, "ssh_host_manager", None)
        if manager is None:
            raise HTTPException(status_code=503, detail="SSH host manager is unavailable")
        snapshots = await asyncio.to_thread(manager.snapshot)
        if connection_id not in snapshots:
            raise HTTPException(status_code=404, detail="SSH connection not found")
        if not await asyncio.to_thread(manager.retry, connection_id):
            raise HTTPException(
                status_code=409,
                detail="SSH connection is detaching and cannot be retried",
            )
        return {"queued": True}

    @router.post("/ssh/test")
    async def test_ssh_connection(body: SshTestRequest, request: Request) -> SshTestResponse:
        """Probe SSH connectivity from this host using a config alias."""
        await _require_admin(request)
        result = await probe_ssh(SshProbeRequest(alias=body.alias))
        return SshTestResponse(
            ok=result.ok,
            message=result.message,
            latency_ms=result.latency_ms,
        )

    return router
