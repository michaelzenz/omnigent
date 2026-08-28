"""In-memory store + REST routes for host-reported script plugin health.

Hosts POST a snapshot of their poll plugin run outcomes here; the glossaries
board reads it back. Health is a *current* snapshot keyed by
``(host_id, plugin_name)`` — there is no history. Records expire after
``_TTL_S`` (3x the host heartbeat) so a dead host's stale rows vanish on
their own without a tombstone POST.
"""

from __future__ import annotations

import threading
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from omnigent.host.identity import HOST_ID_HEADER
from omnigent.server.auth import AuthProvider
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes._auth_helpers import get_user_id, require_user
from omnigent.server.routes._host_filesystem import (
    HostFsError,
    HostFsUnavailableError,
    read_workspace_from_host,
)
from omnigent.stores.host_store import HostStore

PluginKind = Literal["poll"]

# Hosts heartbeat ~every 3 min; drop rows that haven't been refreshed in 3x
# that, so a crashed host's board entries fade without a tombstone.
_TTL_S = 9 * 60.0


class PluginHealthInput(BaseModel):
    """One plugin's health, as posted by a host."""

    name: str
    kind: PluginKind
    outcome: str
    enabled: bool = True
    builtin: bool = False
    last_run_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    singleton_skipped: bool = False
    warning: str | None = None
    interval_s: float | None = None


class PluginHealthSnapshot(BaseModel):
    """Body of ``POST /v1/agent-tasks/script-plugins/health``."""

    plugins: list[PluginHealthInput] = Field(default_factory=list)


class PluginEnabledRequest(BaseModel):
    enabled: bool


class _Stored(BaseModel):
    """A health record plus the host that reported it and when."""

    host_id: str
    plugin: PluginHealthInput
    updated_at: float


class PluginHealthStore:
    """Thread-safe in-memory store of the latest health per (host, plugin)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], _Stored] = {}

    def upsert(self, host_id: str, plugins: list[PluginHealthInput]) -> None:
        now = time.time()
        with self._lock:
            for p in plugins:
                self._records[(host_id, p.name)] = _Stored(
                    host_id=host_id, plugin=p, updated_at=now
                )

    def list(self, kind: PluginKind | None = None) -> list[_Stored]:
        now = time.time()
        with self._lock:
            live = [
                r
                for r in self._records.values()
                if now - r.updated_at < _TTL_S and (kind is None or r.plugin.kind == kind)
            ]
            live.sort(key=lambda r: (r.host_id, r.plugin.name))
            return live

    def set_enabled(self, host_id: str, name: str, enabled: bool) -> None:
        """Apply a confirmed host setting to the current health snapshot."""
        now = time.time()
        with self._lock:
            current = self._records.get((host_id, name))
            if current is None:
                return
            outcome = current.plugin.outcome
            if not enabled:
                outcome = "disabled"
            elif outcome == "disabled":
                outcome = "enabled_pending"
            self._records[(host_id, name)] = _Stored(
                host_id=host_id,
                plugin=current.plugin.model_copy(update={"enabled": enabled, "outcome": outcome}),
                updated_at=now,
            )


# Module-level singleton — one store per server process. Health is ephemeral;
# a restart simply means hosts repopulate it on their next heartbeat.
_store = PluginHealthStore()


def get_plugin_health_store() -> PluginHealthStore:
    return _store


def create_script_plugin_health_router(
    *,
    host_registry: HostRegistry,
    host_store: HostStore | None,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the ``/v1/agent-tasks/script-plugins/health`` router.

    The POST is authenticated by the host (``X-Omnigent-Host-Id`` header) —
    a host token identifies the caller; the host_id is taken from the header.
    The GET is user-authenticated (the glossaries board is read by humans).
    """

    router = APIRouter()

    @router.post("/agent-tasks/script-plugins/health")
    async def post_health(
        request: Request,
        body: PluginHealthSnapshot,
    ) -> dict[str, Any]:
        host_id = request.headers.get(HOST_ID_HEADER)
        if host_id is not None:
            host_id = host_id.strip() or None
        if host_id is None:
            # Fall back to user auth if a user posts directly (rare); otherwise
            # require a host identity so we can attribute the snapshot.
            host_id = get_user_id(request, auth_provider) or require_user(request, auth_provider)
        if host_id is None:
            host_id = "local"
        _store.upsert(host_id, body.plugins)
        return {"ok": True, "count": len(body.plugins)}

    @router.get("/agent-tasks/script-plugins/health")
    async def get_health(
        request: Request,
        kind: Annotated[PluginKind | None, Query()] = None,
    ) -> dict[str, Any]:
        require_user(request, auth_provider)
        rows = _store.list(kind=kind)
        return {
            "plugins": [
                {
                    "host_id": r.host_id,
                    "name": r.plugin.name,
                    "kind": r.plugin.kind,
                    "outcome": r.plugin.outcome,
                    "enabled": r.plugin.enabled,
                    "builtin": r.plugin.builtin,
                    "last_run_at": r.plugin.last_run_at,
                    "last_success_at": r.plugin.last_success_at,
                    "last_failure_at": r.plugin.last_failure_at,
                    "last_error": r.plugin.last_error,
                    "consecutive_failures": r.plugin.consecutive_failures,
                    "singleton_skipped": r.plugin.singleton_skipped,
                    "warning": r.plugin.warning,
                    "interval_s": r.plugin.interval_s,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ]
        }

    @router.put("/agent-tasks/script-plugins/hosts/{host_id}/{plugin_name}")
    async def update_poll_plugin(
        host_id: str,
        plugin_name: str,
        request: Request,
        body: PluginEnabledRequest,
    ) -> dict[str, Any]:
        owner = require_user(request, auth_provider) or "local"
        if host_store is None:
            raise HTTPException(status_code=503, detail="Host configuration is unavailable.")
        if not any(host.host_id == host_id for host in host_store.list_hosts(owner)):
            raise HTTPException(status_code=404, detail="Host not found.")
        connection = host_registry.get(host_id)
        if connection is None:
            raise HTTPException(status_code=409, detail=f"Host {host_id!r} is offline.")
        try:
            payload = await read_workspace_from_host(
                host_registry=host_registry,
                host_conn=connection,
                op="poll_plugin.settings",
                workspace="",
                session_id="",
                params={"name": plugin_name, "enabled": body.enabled},
            )
        except HostFsError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        except HostFsUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if payload.get("name") != plugin_name or payload.get("enabled") is not body.enabled:
            raise HTTPException(
                status_code=502, detail="Host returned invalid poll plugin settings."
            )
        _store.set_enabled(host_id, plugin_name, body.enabled)
        return {"host_id": host_id, "name": plugin_name, "enabled": body.enabled}

    return router
