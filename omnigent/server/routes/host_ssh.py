"""Host-scoped SSH attach API.

The user's desktop app (host daemon) is the only SSH executor: it pulls its
owner's connection assignments from here and pushes reconciliation progress
back. Authentication rides the daemon's tunnel identity — the
``X-Omnigent-Host-Id`` header resolves to a live host connection whose owner
scopes every query, so a daemon only ever sees and mutates its own user's
SSH configuration.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.host.identity import HOST_ID_HEADER
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.host_registry import HostRegistry
from omnigent.server.ssh_logs import SshLogRing
from omnigent.stores.host_store import HostStore
from omnigent.stores.ssh_host_installation_store import SshHostInstallationStore
from omnigent.version import VERSION

# Mirrors the previous server-side manager: remote daemons keep their launch
# token for a year so long-lived laptops don't need re-provisioning.
_TOKEN_TTL_SECONDS = 366 * 24 * 60 * 60


class SshClaimRequest(BaseModel):
    """Body for ``POST /host/ssh/connections/{id}/claim``."""

    lease_seconds: int = Field(..., ge=1, le=3600)


class SshPhaseRequest(BaseModel):
    """Body for ``POST /host/ssh/connections/{id}/phase``."""

    generation: int
    phase: str = Field(..., min_length=1, max_length=32)
    next_attempt_at: int | None = None
    last_error: str | None = None
    increment_attempt: bool = False
    reset_attempt: bool = False
    release: bool = False


class SshLogEntryBody(BaseModel):
    """One lifecycle log entry pushed by the executor."""

    phase: str
    level: str
    message: str


class SshLogsRequest(BaseModel):
    """Body for ``POST /host/ssh/connections/{id}/logs``."""

    entries: list[SshLogEntryBody]


def create_host_ssh_router(
    *,
    ssh_store: SshHostInstallationStore | None = None,
    host_store: HostStore | None = None,
    host_registry: HostRegistry | None = None,
    ssh_logs: SshLogRing | None = None,
) -> APIRouter:
    """Build the router the desktop app's SSH executor talks to."""
    router = APIRouter()

    def _store() -> SshHostInstallationStore:
        if ssh_store is None:
            raise HTTPException(status_code=503, detail="SSH storage is unavailable")
        return ssh_store

    async def _resolve(request: Request) -> tuple[str, str]:
        """Resolve the calling daemon's identity to its owner's config scope."""
        if host_registry is None:
            raise HTTPException(status_code=503, detail="Host registry is unavailable")
        host_id = request.headers.get(HOST_ID_HEADER)
        if host_id is not None:
            host_id = host_id.strip() or None
        if host_id is None:
            raise HTTPException(status_code=401, detail="Host identity required")
        conn = host_registry.get(host_id)
        if conn is None:
            raise HTTPException(status_code=401, detail="Host is not connected")
        # Managed sandboxes authenticate as their owner's user, but they are
        # server-provisioned VMs, not the user's machine — SSH attach must
        # only ever execute from the desktop app, so fail closed for them.
        if host_store is not None:
            row = await _call(host_store.get_host, host_id)
            if row is not None and getattr(row, "sandbox_provider", None) is not None:
                raise HTTPException(
                    status_code=403,
                    detail="SSH attach does not run on managed sandbox hosts",
                )
        return host_id, conn.owner or RESERVED_USER_LOCAL

    def _owned_row(connection_id: str, owner: str) -> Any:
        snapshots = _store().snapshots(owner)
        row = snapshots.get(connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="SSH connection not found")
        return row

    def _remote_online(host_id: str) -> bool:
        if host_store is None:
            return False
        from omnigent.stores.host_store import host_is_live

        host = host_store.get_host(host_id)
        return host is not None and host_is_live(host)

    @router.get("/host/ssh/assignments")
    async def get_assignments(request: Request) -> dict[str, object]:
        """List the calling daemon's owner's SSH connections and settings."""
        _, owner = await _resolve(request)
        store = _store()
        # Self-heal version skew: rows pinned to an older server version get
        # one requeue so the executor reinstalls after a server upgrade.
        await _call(store.requeue_stale_versions, owner, VERSION)
        rows = await _call(store.rows_for_owner, owner)
        settings = await _call(store.get_settings, owner)
        return {
            "server_version": VERSION,
            "settings": {
                "package_index_url": settings.package_index_url,
                "npm_registry_url": settings.npm_registry_url,
                "remote_namespace": settings.remote_namespace,
            },
            "connections": [
                {
                    "connection_id": row.connection_id,
                    "label": row.label,
                    "alias": row.ssh_alias,
                    "desired_state": row.desired_state,
                    "phase": row.phase,
                    "generation": row.generation,
                    "attempt": row.attempt,
                    "next_attempt_at": row.next_attempt_at,
                    "lease_owner": row.lease_owner,
                    "lease_expires_at": row.lease_expires_at,
                    "host_id": row.host_id,
                    "bundle_version": row.bundle_version,
                }
                for row in rows
            ],
        }

    @router.post("/host/ssh/connections/{connection_id}/claim")
    async def claim_connection(connection_id: str, body: SshClaimRequest, request: Request):
        """Lease one connection for execution; re-claiming by the same daemon renews."""
        host_id, owner = await _resolve(request)
        row = _owned_row(connection_id, owner)
        claimed = await _call(
            _store().acquire,
            connection_id,
            lease_owner=host_id,
            lease_seconds=body.lease_seconds,
        )
        if claimed is None:
            return {"claimed": False, "remote_host_online": _remote_online(row.host_id)}
        return {
            "claimed": True,
            "generation": claimed.generation,
            "attempt": claimed.attempt,
            "desired_state": claimed.desired_state,
            "phase": claimed.phase,
            "bundle_version": claimed.bundle_version,
            "remote_host_online": _remote_online(claimed.host_id),
        }

    @router.post("/host/ssh/connections/{connection_id}/phase")
    async def push_phase(connection_id: str, body: SshPhaseRequest, request: Request):
        """Persist one executor phase transition (generation-checked)."""
        host_id, owner = await _resolve(request)
        row = _owned_row(connection_id, owner)
        accepted = await _call(
            _store().set_phase,
            connection_id,
            lease_owner=host_id,
            generation=body.generation,
            phase=body.phase,
            next_attempt_at=body.next_attempt_at,
            last_error=body.last_error,
            increment_attempt=body.increment_attempt,
            reset_attempt=body.reset_attempt,
            release=body.release,
        )
        return {
            "accepted": accepted,
            "superseded": not accepted,
            "remote_host_online": _remote_online(row.host_id),
        }

    @router.post("/host/ssh/connections/{connection_id}/release-lease")
    async def release_lease(connection_id: str, request: Request):
        """Drop the calling daemon's lease after durable intent superseded it."""
        host_id, owner = await _resolve(request)
        _owned_row(connection_id, owner)
        released = await _call(
            _store().release_lease,
            connection_id,
            lease_owner=host_id,
        )
        return {"released": released}

    @router.post("/host/ssh/connections/{connection_id}/logs")
    async def push_logs(connection_id: str, body: SshLogsRequest, request: Request):
        """Append executor lifecycle log entries for the settings UI."""
        _, owner = await _resolve(request)
        _owned_row(connection_id, owner)
        ring = ssh_logs
        if ring is None:
            raise HTTPException(status_code=503, detail="SSH log storage is unavailable")
        for entry in body.entries:
            ring.append(
                connection_id,
                phase=entry.phase,
                level=entry.level,
                message=entry.message,
            )
        return {"appended": len(body.entries)}

    @router.post("/host/ssh/connections/{connection_id}/register-remote-host")
    async def register_remote_host(connection_id: str, request: Request):
        """Register the remote SSH host identity and mint its launch token."""
        _, owner = await _resolve(request)
        if host_store is None:
            raise HTTPException(status_code=503, detail="Host storage is unavailable")
        row = _owned_row(connection_id, owner)
        import secrets

        from omnigent.db.utils import now_epoch

        token = secrets.token_urlsafe(32)
        host_name = f"{row.label[:48]}-{row.connection_id[:8]}"
        await _call(
            host_store.register_ssh_host,
            host_id=row.host_id,
            name=host_name,
            owner=owner,
            token=token,
            token_expires_at=now_epoch() + _TOKEN_TTL_SECONDS,
        )
        return {
            "host_id": row.host_id,
            "name": host_name,
            "token": token,
            "token_expires_at": now_epoch() + _TOKEN_TTL_SECONDS,
        }

    @router.delete("/host/ssh/connections/{connection_id}/remote-host")
    async def delete_remote_host(connection_id: str, request: Request):
        """Delete the remote SSH host row after a finished detach."""
        _, owner = await _resolve(request)
        row = _owned_row(connection_id, owner)
        if host_store is None:
            raise HTTPException(status_code=503, detail="Host storage is unavailable")
        await _call(host_store.delete_host, row.host_id)
        return {"deleted": True}

    @router.get("/host/ssh/connections/{connection_id}/remote-host-status")
    async def remote_host_status(connection_id: str, request: Request):
        """Report whether the remote SSH host daemon is currently online."""
        _, owner = await _resolve(request)
        row = _owned_row(connection_id, owner)
        return {"online": _remote_online(row.host_id)}

    return router


async def _call(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking store call off the event loop."""
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)
