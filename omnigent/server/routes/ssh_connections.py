"""SSH connection routes for the settings UI and host daemon."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.ssh_connections_store import (
    SshConnectionProfile,
    new_ssh_connection_id,
    profile_to_api_dict,
    read_ssh_connections,
    validate_ssh_alias,
    write_ssh_connections,
)
from omnigent.ssh_probe import SshProbeRequest, probe_ssh


class SshConnectionBody(BaseModel):
    """One SSH connection profile in API requests."""

    id: str | None = None
    label: str = Field(..., min_length=1, max_length=128)
    alias: str = Field(..., min_length=1, max_length=128)
    codex_remote: bool = True


class SshConnectionsPutRequest(BaseModel):
    """Body for ``PUT /v1/ssh/connections``."""

    connections: list[SshConnectionBody]


class SshTestRequest(BaseModel):
    """Body for ``POST /v1/ssh/test``."""

    alias: str = Field(..., min_length=1, max_length=128)


class SshTestResponse(BaseModel):
    """Result of an SSH connectivity probe."""

    ok: bool
    message: str
    latency_ms: int | None = None


def _parse_profiles(body: SshConnectionsPutRequest) -> list[SshConnectionProfile]:
    existing = {profile.id: profile for profile in read_ssh_connections()}
    profiles: list[SshConnectionProfile] = []
    seen_ids: set[str] = set()
    for entry in body.connections:
        alias_error = validate_ssh_alias(entry.alias)
        if alias_error is not None:
            raise HTTPException(status_code=400, detail=alias_error)
        label = entry.label.strip()
        if not label:
            raise HTTPException(status_code=400, detail="Label is required")
        profile_id = entry.id.strip() if entry.id else new_ssh_connection_id()
        if profile_id in seen_ids:
            raise HTTPException(status_code=400, detail=f"Duplicate connection id: {profile_id}")
        seen_ids.add(profile_id)
        prior = existing.get(profile_id)
        created_at = prior.created_at if prior is not None else datetime.now(UTC).isoformat()
        profiles.append(
            SshConnectionProfile(
                id=profile_id,
                label=label,
                alias=entry.alias.strip(),
                created_at=created_at,
                codex_remote=entry.codex_remote,
            )
        )
    return profiles


def create_ssh_connections_router(*, auth_provider: AuthProvider | None = None) -> APIRouter:
    """Build the router for SSH settings helpers."""
    router = APIRouter()

    @router.get("/ssh/connections")
    async def list_ssh_connections(request: Request) -> dict[str, list[dict[str, object]]]:
        """List SSH connection profiles stored on this host."""
        require_user(request, auth_provider)
        profiles = read_ssh_connections()
        return {"connections": [profile_to_api_dict(profile) for profile in profiles]}

    @router.put("/ssh/connections")
    async def put_ssh_connections(
        body: SshConnectionsPutRequest,
        request: Request,
    ) -> dict[str, list[dict[str, object]]]:
        """Replace SSH connection profiles stored on this host."""
        require_user(request, auth_provider)
        profiles = _parse_profiles(body)
        write_ssh_connections(profiles)
        return {"connections": [profile_to_api_dict(profile) for profile in profiles]}

    @router.post("/ssh/test")
    async def test_ssh_connection(body: SshTestRequest, request: Request) -> SshTestResponse:
        """Probe SSH connectivity from this host using a config alias."""
        require_user(request, auth_provider)
        result = await probe_ssh(SshProbeRequest(alias=body.alias))
        return SshTestResponse(
            ok=result.ok,
            message=result.message,
            latency_ms=result.latency_ms,
        )

    return router
