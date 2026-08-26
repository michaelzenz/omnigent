"""In-process loopback relay for server-brokered inference."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server_transport import server_async_http_transport_kwargs

_logger = logging.getLogger(__name__)
_MAX_REQUEST_BYTES = 20 * 1024 * 1024
_SURFACE_PREFIXES = {
    "ai-gateway/anthropic/": "anthropic",
    "ai-gateway/codex/v1/": "responses",
    "serving-endpoints/": "completions",
}
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


@dataclass(frozen=True)
class RelayEndpoint:
    """Loopback endpoint and capability passed to one runner."""

    base_url: str
    capability: str


@dataclass(frozen=True)
class _RelayBinding:
    session_id: str
    runner_id: str
    binding_token: str


class _EmbeddedUvicornServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield

    def install_signal_handlers(self) -> None:
        return


class HostInferenceRelay:
    """One loopback inference relay owned by an ``omnigent host`` process."""

    def __init__(self, server_url: str) -> None:
        self._server_url = server_url.rstrip("/")
        self._bindings: dict[str, _RelayBinding] = {}
        self._runner_capabilities: dict[str, set[str]] = {}
        self._app = FastAPI()
        self._app.add_api_route(
            "/v1/inference/{path:path}",
            self._forward,
            methods=["POST"],
        )
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None
        self._socket: socket.socket | None = None
        self._port: int | None = None

    @property
    def started(self) -> bool:
        return (
            self._server is not None
            and self._server.started
            and self._server_task is not None
            and not self._server_task.done()
            and self._port is not None
        )

    async def start(self) -> None:
        """Start the relay on an ephemeral loopback port."""
        if self.started:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            sock.listen(128)
            sock.setblocking(False)
            self._port = int(sock.getsockname()[1])
            self._client = httpx.AsyncClient(
                base_url=self._server_url,
                timeout=httpx.Timeout(30.0, read=None),
                **server_async_http_transport_kwargs(),
            )
            config = uvicorn.Config(
                self._app,
                host="127.0.0.1",
                port=self._port,
                access_log=False,
                log_config=None,
                timeout_graceful_shutdown=5,
            )
            self._server = _EmbeddedUvicornServer(config)
            self._server_task = asyncio.create_task(
                self._server.serve(sockets=[sock]),
                name="host-inference-relay",
            )
            for _ in range(100):
                if self._server.started:
                    return
                if self._server_task.done():
                    break
                await asyncio.sleep(0.01)
        except BaseException:
            await self.close()
            raise
        await self.close()
        raise RuntimeError("host inference relay did not start")

    def add_exit_callback(self, callback: Callable[[], None]) -> None:
        """Invoke *callback* if the running relay exits."""
        if self._server_task is None:
            raise RuntimeError("host inference relay is not running")

        def notify(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and (error := task.exception()) is not None:
                _logger.error("Host inference relay exited", exc_info=error)
            callback()

        self._server_task.add_done_callback(notify)

    def register(self, *, session_id: str, runner_id: str, binding_token: str) -> RelayEndpoint:
        """Register one runner generation and return its local capability."""
        if not self.started or self._port is None:
            raise RuntimeError("host inference relay is not running")
        capability = f"proxy_{secrets.token_urlsafe(32)}"
        digest = _capability_digest(capability)
        self._bindings[digest] = _RelayBinding(
            session_id=session_id,
            runner_id=runner_id,
            binding_token=binding_token,
        )
        self._runner_capabilities.setdefault(runner_id, set()).add(digest)
        return RelayEndpoint(
            base_url=f"http://127.0.0.1:{self._port}/v1/inference",
            capability=capability,
        )

    def revoke(self, runner_id: str) -> None:
        """Revoke every relay capability belonging to *runner_id*."""
        for digest in self._runner_capabilities.pop(runner_id, set()):
            self._bindings.pop(digest, None)

    async def close(self) -> None:
        """Stop the relay and release all in-memory credentials."""
        self._bindings.clear()
        self._runner_capabilities.clear()
        if self._server is not None:
            self._server.should_exit = True
        try:
            if self._server_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._server_task
        finally:
            try:
                if self._client is not None:
                    await self._client.aclose()
            finally:
                if self._socket is not None:
                    self._socket.close()
                self._server = None
                self._server_task = None
                self._client = None
                self._socket = None
                self._port = None

    async def _forward(self, request: Request, path: str) -> StreamingResponse:
        binding = self._authorize(request)
        surface, upstream_path = _surface_and_path(path)
        body = await _read_limited_body(request)
        client = self._client
        if client is None:
            raise HTTPException(status_code=503, detail="inference relay is unavailable")
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
                "host",
                "content-length",
            }
        }
        headers[RUNNER_TUNNEL_TOKEN_HEADER] = binding.binding_token
        upstream = client.build_request(
            "POST",
            (
                f"/v1/runners/{binding.runner_id}/sessions/{binding.session_id}"
                f"/inference/{surface}/{upstream_path}"
            ),
            params=request.query_params,
            headers=headers,
            content=body,
        )
        response = await client.send(upstream, stream=True)
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

        return StreamingResponse(
            response_body(),
            status_code=response.status_code,
            headers=response_headers,
        )

    def _authorize(self, request: Request) -> _RelayBinding:
        authorization = request.headers.get("authorization", "")
        scheme, _, capability = authorization.partition(" ")
        if scheme.lower() != "bearer" or not capability:
            raise HTTPException(status_code=401, detail="missing inference capability")
        binding = self._bindings.get(_capability_digest(capability))
        if binding is None:
            raise HTTPException(status_code=401, detail="invalid inference capability")
        return binding


async def _read_limited_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="inference request is too large")
    return bytes(body)


def _connection_header_names(value: str | None) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in (value or "").split(",") if part.strip())


def _capability_digest(capability: str) -> str:
    return hashlib.sha256(capability.encode("utf-8")).hexdigest()


def _surface_and_path(path: str) -> tuple[str, str]:
    for prefix, surface in _SURFACE_PREFIXES.items():
        if path.startswith(prefix):
            return surface, path.removeprefix(prefix)
    raise HTTPException(status_code=404, detail="unsupported inference surface")
