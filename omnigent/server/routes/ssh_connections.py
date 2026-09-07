"""SSH connection routes for the settings UI.

The server only stores per-user configuration and reconciliation state;
execution lives in the user's desktop app (host daemon), which pulls
assignments and pushes phase/log updates through the host-scoped API.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.entities import SshConnectionProfile, SshSettings
from omnigent.entities.ssh_connection import (
    new_ssh_connection_id,
    profile_to_api_dict,
    validate_npm_registry_url,
    validate_package_index_url,
    validate_ssh_alias,
    validate_ssh_connection_id,
)
from omnigent.server.auth import RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.ssh_logs import SshLogRing
from omnigent.stores.host_store import (
    RAPID_DISCONNECT_WARNING_THRESHOLD,
    host_is_live,
)
from omnigent.stores.ssh_host_installation_store import SshHostInstallationStore
from omnigent.version import VERSION


class SshConnectionBody(BaseModel):
    """One SSH connection profile in API requests."""

    id: str | None = None
    label: str = Field(..., min_length=1, max_length=128)
    alias: str = Field(..., min_length=1, max_length=128)


class SshConnectionsPutRequest(BaseModel):
    """Body for ``PUT /v1/ssh/connections``."""

    connections: list[SshConnectionBody]
    package_index_url: str | None = None
    npm_registry_url: str | None = None


def _owner(request: Request, auth_provider: AuthProvider | None) -> str:
    """Resolve the requesting user's SSH config owner id."""
    return require_user(request, auth_provider) or RESERVED_USER_LOCAL


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


def _normalize_npm_registry_url(raw: str | None) -> str | None:
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    error = validate_npm_registry_url(trimmed)
    if error is not None:
        raise HTTPException(status_code=400, detail=error)
    return trimmed


def _connections_response(
    profiles: list[SshConnectionProfile],
    *,
    settings: SshSettings,
    snapshots: Mapping[str, Any],
    host_store: Any,
    online_by_host_id: dict[str, bool] | None = None,
    rapid_disconnects_by_host_id: dict[str, int] | None = None,
) -> dict[str, object]:
    result: list[dict[str, object]] = []
    for profile in profiles:
        item = profile_to_api_dict(profile)
        state = snapshots.get(profile.id)
        online = False
        if state is not None and host_store is not None:
            if online_by_host_id is not None:
                online = online_by_host_id.get(state.host_id, False)
            else:
                online = host_store.is_online(state.host_id)
        if state is not None:
            item.update(
                {
                    "host_id": state.host_id,
                    "lifecycle": state.desired_state,
                    "phase": state.phase,
                    "last_error": state.last_error,
                    "attempt": state.attempt,
                    "next_retry_at": (
                        datetime.fromtimestamp(state.next_attempt_at, UTC).isoformat()
                        if state.next_attempt_at is not None
                        else None
                    ),
                    "updated_at": datetime.fromtimestamp(state.updated_at, UTC).isoformat(),
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
        # Surface a "connection flaky" warning when the host has been
        # rapidly disconnecting — the hallmark of SSH tunnel thrashing
        # or a bad network causing repeated drops.
        rapid_count = (
            rapid_disconnects_by_host_id.get(state.host_id, 0)
            if state is not None and rapid_disconnects_by_host_id is not None
            else 0
        )
        if rapid_count >= RAPID_DISCONNECT_WARNING_THRESHOLD:
            item["warning"] = (
                "Connection is flaky — possibly bad network or another "
                "server is competing for the socket."
            )
        result.append(item)
    return {
        "connections": result,
        "package_index_url": settings.package_index_url,
        "npm_registry_url": settings.npm_registry_url,
    }


async def _build_connections_payload(
    profiles: list[SshConnectionProfile],
    request: Request,
    store: SshHostInstallationStore,
    owner: str,
) -> dict[str, object]:
    snapshots = await asyncio.to_thread(store.snapshots, owner)
    settings = await asyncio.to_thread(store.get_settings, owner)
    host_store = getattr(request.app.state, "host_store", None)
    online_by_host_id: dict[str, bool] = {}
    rapid_disconnects_by_host_id: dict[str, int] = {}
    if host_store is not None:
        for state in snapshots.values():
            host_id = state.host_id
            host = await asyncio.to_thread(host_store.get_host, host_id)
            online_by_host_id[host_id] = host is not None and host_is_live(host)
            rapid_disconnects_by_host_id[host_id] = (
                host.consecutive_rapid_disconnects if host is not None else 0
            )
    return _connections_response(
        profiles,
        settings=settings,
        snapshots=snapshots,
        host_store=host_store,
        online_by_host_id=online_by_host_id,
        rapid_disconnects_by_host_id=rapid_disconnects_by_host_id,
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
    existing_profiles: list[SshConnectionProfile],
    owner: str,
) -> list[SshConnectionProfile]:
    existing = {profile.id: profile for profile in existing_profiles}
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
                owner=owner,
            )
        )
    return profiles


_SSH_PROBE_TIMEOUT_S = 20.0


def _find_owner_host(
    host_registry: HostRegistry | None,
    owner: str,
) -> Any:
    """Return the caller's connected desktop app, if any.

    The probe must run on the machine the SSH executor runs on, so only
    the requesting user's own host daemon qualifies.
    """
    if host_registry is None:
        return None
    for host_id in host_registry.online_host_ids():
        conn = host_registry.get(host_id)
        if conn is not None and conn.owner == owner:
            return conn
    return None


async def _ask_host_ssh_probe(
    host_registry: HostRegistry,
    conn: Any,
    alias: str,
) -> dict[str, Any]:
    """Send a ``host.ssh_probe`` frame and await the correlated result."""
    from omnigent.host.frames import HostSshProbeFrame, encode_host_frame

    request_id = secrets.token_hex(8)
    loop = asyncio.get_event_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    conn.pending_ssh_probes[request_id] = future
    frame = encode_host_frame(HostSshProbeFrame(request_id=request_id, alias=alias))
    try:
        try:
            host_registry.send_text(conn, frame)
        except ConnectionError as exc:
            raise HTTPException(
                status_code=503, detail="Desktop app connection lost during SSH probe"
            ) from exc
        try:
            return await asyncio.wait_for(future, timeout=_SSH_PROBE_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Desktop app did not respond to the SSH probe within "
                f"{_SSH_PROBE_TIMEOUT_S:.0f}s",
            ) from exc
    finally:
        conn.pending_ssh_probes.pop(request_id, None)


def create_ssh_connections_router(
    *,
    ssh_store: SshHostInstallationStore | None = None,
    auth_provider: AuthProvider | None = None,
    host_registry: HostRegistry | None = None,
    ssh_logs: SshLogRing | None = None,
) -> APIRouter:
    """Build the router for SSH settings helpers."""
    router = APIRouter()

    def _store() -> SshHostInstallationStore:
        if ssh_store is None:
            raise HTTPException(status_code=503, detail="SSH storage is unavailable")
        return ssh_store

    def _logs() -> SshLogRing:
        if ssh_logs is None:
            raise HTTPException(status_code=503, detail="SSH log storage is unavailable")
        return ssh_logs

    @router.get("/ssh/connections")
    async def list_ssh_connections(request: Request) -> dict[str, object]:
        """List the requesting user's SSH connection profiles."""
        owner = _owner(request, auth_provider)
        store = _store()
        profiles = await asyncio.to_thread(store.profiles, owner)
        return await _build_connections_payload(profiles, request, store, owner)

    @router.put("/ssh/connections")
    async def put_ssh_connections(
        body: SshConnectionsPutRequest,
        request: Request,
    ) -> dict[str, object]:
        """Replace the requesting user's SSH connection profiles."""
        owner = _owner(request, auth_provider)
        store = _store()
        existing = await asyncio.to_thread(store.profiles, owner)
        profiles = _parse_profiles(body, existing_profiles=existing, owner=owner)
        prior_settings = await asyncio.to_thread(store.get_settings, owner)
        package_index_url = _normalize_package_index_url(body.package_index_url)
        npm_registry_url = _normalize_npm_registry_url(body.npm_registry_url)
        await asyncio.to_thread(
            store.sync_connections,
            {profile.id: profile for profile in profiles},
            bundle_version=VERSION,
            owner=owner,
        )
        await asyncio.to_thread(
            store.update_settings,
            owner=owner,
            package_index_url=package_index_url,
            npm_registry_url=npm_registry_url,
        )
        if (
            package_index_url != prior_settings.package_index_url
            or npm_registry_url != prior_settings.npm_registry_url
        ):
            # New registries change what installs fetch on remote hosts;
            # queue the owner's connections so the executor re-installs.
            await asyncio.to_thread(store.requeue_connected_for_owner, owner)
        return await _build_connections_payload(profiles, request, store, owner)

    @router.post("/ssh/connections/{connection_id}/retry")
    async def retry_ssh_connection(connection_id: str, request: Request) -> dict[str, bool]:
        """Queue an immediate reconciliation attempt."""
        owner = _owner(request, auth_provider)
        store = _store()
        snapshots = await asyncio.to_thread(store.snapshots, owner)
        if connection_id not in snapshots:
            raise HTTPException(status_code=404, detail="SSH connection not found")
        if not await asyncio.to_thread(store.retry_now, connection_id, owner=owner):
            raise HTTPException(
                status_code=409,
                detail="SSH connection is detaching and cannot be retried",
            )
        return {"queued": True}

    @router.get("/ssh/connections/{connection_id}/logs")
    async def get_ssh_connection_logs(
        connection_id: str,
        request: Request,
    ) -> dict[str, object]:
        """Return captured installation lifecycle events for the settings UI."""
        owner = _owner(request, auth_provider)
        store = _store()
        snapshots = await asyncio.to_thread(store.snapshots, owner)
        if connection_id not in snapshots:
            raise HTTPException(status_code=404, detail="SSH connection not found")
        entries = _logs().entries(connection_id)
        return {
            "entries": [
                {
                    "timestamp": entry.timestamp,
                    "time": datetime.fromtimestamp(entry.timestamp, UTC).isoformat(),
                    "phase": entry.phase,
                    "level": entry.level,
                    "message": entry.message,
                }
                for entry in entries
            ],
        }

    @router.post("/ssh/test")
    async def test_ssh_connection(body: SshTestRequest, request: Request) -> SshTestResponse:
        """Probe SSH connectivity from the requesting user's desktop app."""
        owner = _owner(request, auth_provider)
        registry: HostRegistry | None = (
            getattr(request.app.state, "host_registry", None) or host_registry
        )
        conn = _find_owner_host(registry, owner)
        if conn is None or registry is None:
            raise HTTPException(
                status_code=503,
                detail="Desktop app is not connected; SSH connections run from your machine",
            )
        result = await _ask_host_ssh_probe(registry, conn, body.alias)
        return SshTestResponse(
            ok=bool(result.get("ok")),
            message=str(result.get("message") or ""),
            latency_ms=result.get("latency_ms"),
        )

    return router
