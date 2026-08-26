"""Runner-bound server inference proxy for Onih Pi."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from omnigent.inference_proxy import inference_surface_for_model
from omnigent.inner.databricks_executor import _resolve_databricks_auth
from omnigent.onboarding.databricks_config import get_workspace_url_for_profile
from omnigent.onboarding.provider_config import (
    DATABRICKS_KIND,
    ProviderEntry,
    default_provider_for_harness,
    load_config,
)
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER, token_bound_runner_id
from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)
_MAX_REQUEST_BYTES = 20 * 1024 * 1024
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_SURFACES = {
    "anthropic": ("v1/messages", "/ai-gateway/anthropic/v1/messages"),
    "responses": ("responses", "/ai-gateway/openai/v1/responses"),
    "completions": ("chat/completions", "/serving-endpoints/chat/completions"),
}


def _pi_provider() -> ProviderEntry | None:
    try:
        return default_provider_for_harness(load_config(), "pi")
    except Exception:
        _logger.warning("Could not resolve the server Pi provider", exc_info=True)
        return None


def pi_server_inference_configured() -> bool:
    """Return whether the server has a Databricks provider usable by Pi."""
    provider = _pi_provider()
    return provider is not None and provider.kind == DATABRICKS_KIND and bool(provider.profile)


class _RefreshingAuth(Protocol):
    def current_token(self) -> str | None: ...


@lru_cache(maxsize=8)
def _profile_auth(profile: str, workspace_origin: str) -> tuple[_RefreshingAuth, str]:
    del workspace_origin  # Included in the cache key so profile host changes rebuild auth.
    return _resolve_databricks_auth(profile)


def create_inference_proxy_router(
    conversation_store: ConversationStore,
    *,
    enabled: bool,
) -> APIRouter:
    """Create the single-user runner-bound Databricks inference proxy router."""
    router = APIRouter()

    @router.post(
        "/runners/{runner_id}/sessions/{session_id}/inference/{surface}/{upstream_path:path}"
    )
    async def proxy_inference(
        request: Request,
        runner_id: str,
        session_id: str,
        surface: str,
        upstream_path: str,
    ) -> StreamingResponse:
        if not enabled:
            raise HTTPException(status_code=404, detail="not found")
        _authorize_runner(request, conversation_store, runner_id, session_id)
        mapping = _SURFACES.get(surface)
        if mapping is None or upstream_path != mapping[0]:
            raise HTTPException(status_code=404, detail="unsupported inference surface")
        provider = _pi_provider()
        if provider is None or provider.kind != DATABRICKS_KIND or not provider.profile:
            raise HTTPException(status_code=503, detail="server Pi inference is not configured")
        body = await _read_limited_body(request)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="inference request must be JSON") from exc
        model = payload.get("model") if isinstance(payload, dict) else None
        try:
            expected_surface = (
                inference_surface_for_model(model) if isinstance(model, str) else None
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if expected_surface != surface:
            raise HTTPException(status_code=400, detail="model does not match inference surface")
        try:
            configured_host = get_workspace_url_for_profile(provider.profile)
            workspace_origin = _validated_workspace_origin(configured_host)
            auth, resolved_host = await asyncio.to_thread(
                _profile_auth, provider.profile, workspace_origin
            )
            if _validated_workspace_origin(resolved_host) != workspace_origin:
                raise ValueError("Databricks profile host changed during authentication")
            token = await asyncio.to_thread(auth.current_token)
        except HTTPException:
            raise
        except Exception as exc:
            _logger.warning(
                "Server Pi inference credential requires reauthentication", exc_info=True
            )
            raise HTTPException(
                status_code=503,
                detail="server Databricks authentication requires login",
            ) from exc
        if not token:
            raise HTTPException(
                status_code=503,
                detail="server Databricks authentication requires login",
            )
        request_connection_headers = _connection_header_names(request.headers.get("connection"))
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS
            and key.lower() not in request_connection_headers
            and key.lower()
            not in {
                "authorization",
                "x-api-key",
                "api-key",
                "cookie",
                "host",
                "content-length",
                RUNNER_TUNNEL_TOKEN_HEADER.lower(),
            }
        }
        headers["authorization"] = f"Bearer {token}"
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), follow_redirects=False)
        upstream = client.build_request(
            "POST",
            workspace_origin + mapping[1],
            params=request.query_params,
            headers=headers,
            content=body,
        )
        try:
            response = await client.send(upstream, stream=True)
        except Exception:
            await client.aclose()
            raise
        response_connection_headers = _connection_header_names(response.headers.get("connection"))
        response_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS
            and key.lower() not in response_connection_headers
            and key.lower() not in {"content-length", "set-cookie"}
        }

        async def response_body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(
            response_body(),
            status_code=response.status_code,
            headers=response_headers,
        )

    return router


def _connection_header_names(value: str | None) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in (value or "").split(",") if part.strip())


def _validated_workspace_origin(value: str | None) -> str:
    if not value:
        raise HTTPException(
            status_code=503, detail="server Databricks workspace is not configured"
        )
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail="server Databricks workspace is invalid"
        ) from exc
    allowed_host = hostname.endswith(".databricks.com") or hostname.endswith(
        ".azuredatabricks.net"
    )
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    if (
        parsed.scheme != "https"
        or not allowed_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (address is not None and not address.is_global)
    ):
        raise HTTPException(status_code=503, detail="server Databricks workspace is invalid")
    return f"https://{hostname}"


async def _read_limited_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="inference request is too large")
    return bytes(body)


def _authorize_runner(
    request: Request,
    conversation_store: ConversationStore,
    runner_id: str,
    session_id: str,
) -> None:
    token = (request.headers.get(RUNNER_TUNNEL_TOKEN_HEADER) or "").strip()
    if not token or token_bound_runner_id(token) != runner_id:
        raise HTTPException(status_code=401, detail="unauthenticated runner")
    conversation = conversation_store.get_conversation(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="session not found")
    if conversation.runner_id != runner_id:
        raise HTTPException(status_code=401, detail="runner is not bound to this session")
    if conversation.host_id is None:
        raise HTTPException(status_code=409, detail="session is not host-bound")
