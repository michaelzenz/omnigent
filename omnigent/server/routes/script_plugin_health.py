"""In-memory store + REST routes for host-reported script plugin health.

Hosts POST a snapshot of their poll/timer plugin run outcomes here; the
glossaries board reads it back. Health is a *current* snapshot keyed by
``(host_id, plugin_name)`` — there is no history. Records expire after
``_TTL_S`` (3x the host heartbeat) so a dead host's stale rows vanish on
their own without a tombstone POST.
"""

from __future__ import annotations

import threading
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from omnigent.host.identity import HOST_ID_HEADER
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_user

PluginKind = Literal["poll", "timer"]

# Hosts heartbeat ~every 3 min; drop rows that haven't been refreshed in 3x
# that, so a crashed host's board entries fade without a tombstone.
_TTL_S = 9 * 60.0


class PluginHealthInput(BaseModel):
    """One plugin's health, as posted by a host."""

    name: str
    kind: PluginKind
    outcome: str
    last_run_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    singleton_skipped: bool = False
    interval_s: float | None = None
    fire_at: float | None = None
    fired_at: float | None = None


class PluginHealthSnapshot(BaseModel):
    """Body of ``POST /v1/agent-tasks/script-plugins/health``."""

    plugins: list[PluginHealthInput] = Field(default_factory=list)


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


# Module-level singleton — one store per server process. Health is ephemeral;
# a restart simply means hosts repopulate it on their next heartbeat.
_store = PluginHealthStore()


def get_plugin_health_store() -> PluginHealthStore:
    return _store


def create_script_plugin_health_router(
    *,
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
            user_id = get_user_id(request, auth_provider)
            if user_id is None:
                require_user(request, auth_provider)
            host_id = user_id
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
                    "last_run_at": r.plugin.last_run_at,
                    "last_success_at": r.plugin.last_success_at,
                    "last_failure_at": r.plugin.last_failure_at,
                    "last_error": r.plugin.last_error,
                    "consecutive_failures": r.plugin.consecutive_failures,
                    "singleton_skipped": r.plugin.singleton_skipped,
                    "interval_s": r.plugin.interval_s,
                    "fire_at": r.plugin.fire_at,
                    "fired_at": r.plugin.fired_at,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ]
        }

    return router
