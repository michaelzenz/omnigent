"""Sync of ``~/AGENTS.md`` across the caller's hosts (``/v1/memory-sync``).

Each request asks every online host of the caller for its file, so the server
keeps no copy. Hosts whose files have the same content hash form one variant.
A write replaces a host's file only while it still has the hash that the caller
read. A host is reachable only from the server replica it is connected to.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from omnigent.host.frames import CAP_MEMORY_SYNC
from omnigent.host.memory_ops import MAX_MEMORY_CHARS
from omnigent.server.auth import AuthProvider
from omnigent.server.host_registry import HostConnection, HostRegistry
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._host_filesystem import (
    HostFsError,
    HostFsUnavailableError,
    read_workspace_from_host,
    write_workspace_from_host,
)
from omnigent.stores.host_store import Host, HostStore, host_is_live

MEMORY_FILE = "~/AGENTS.md"
_OFFLINE = "Host is offline."
_NEEDS_UPDATE = "Update the host to use Memory."
_MAX_WRITE_TARGETS = 200

MemoryHostState = Literal["present", "missing", "offline", "needs_update", "error"]
MemoryWriteStatus = Literal["done", "conflict", "failed"]


class MemoryVariant(BaseModel):
    """The hosts whose ``~/AGENTS.md`` has the same content."""

    content_sha256: str
    content: str
    host_ids: list[str]


class MemoryHost(BaseModel):
    """One host's ``~/AGENTS.md``; ``error`` says why its file is unknown."""

    host_id: str
    host_name: str
    online: bool
    state: MemoryHostState
    content_sha256: str | None
    error: str | None


class MemorySyncState(BaseModel):
    """``~/AGENTS.md`` across the caller's hosts, the most common variant first."""

    object: Literal["memory_sync.state"] = "memory_sync.state"
    file: str
    variants: list[MemoryVariant]
    hosts: list[MemoryHost]


class MemoryWriteTarget(BaseModel):
    """A host to write; ``expected_sha256`` is the hash the caller read, null for no file."""

    host_id: str
    expected_sha256: str | None

    model_config = ConfigDict(extra="forbid")


class MemoryWriteRequest(BaseModel):
    """New ``~/AGENTS.md`` text and the hosts to write it to."""

    content: str = Field(max_length=MAX_MEMORY_CHARS)
    targets: list[MemoryWriteTarget] = Field(min_length=1, max_length=_MAX_WRITE_TARGETS)

    model_config = ConfigDict(extra="forbid")


class MemoryWriteHostResult(BaseModel):
    """The outcome on one host; ``conflict`` means its file changed after the caller read it."""

    host_id: str
    status: MemoryWriteStatus
    content_sha256: str | None = None
    error: str | None = None


class MemoryWriteResult(BaseModel):
    """The outcome of a write on each target host."""

    object: Literal["memory_sync.write_result"] = "memory_sync.write_result"
    results: list[MemoryWriteHostResult]


def create_memory_sync_router(
    host_registry: HostRegistry,
    host_store: HostStore,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the routes that sync ``~/AGENTS.md`` across the caller's hosts.

    :param host_registry: The hosts connected to this replica.
    :param host_store: The caller's registered hosts.
    :param auth_provider: Identifies the caller; ``None`` is single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    async def call(
        conn: HostConnection, op: str, params: dict[str, Any], *, write: bool = False
    ) -> dict[str, Any]:
        send = write_workspace_from_host if write else read_workspace_from_host
        return await send(
            host_registry=host_registry,
            host_conn=conn,
            op=op,
            workspace="",
            session_id="",
            params=params,
        )

    def reachable(host: Host) -> tuple[HostConnection | None, str | None]:
        conn = host_registry.get(host.host_id)
        if conn is None or not host_is_live(host):
            return None, _OFFLINE
        if CAP_MEMORY_SYNC not in conn.hello.capabilities:
            return None, _NEEDS_UPDATE
        return conn, None

    async def owned_hosts(request: Request) -> list[Host]:
        user_id = require_user(request, auth_provider)
        # With auth disabled, the single-user server's hosts belong to "local".
        return await asyncio.to_thread(host_store.list_hosts, user_id or "local")

    async def read_host(host: Host) -> tuple[MemoryHost, str | None]:
        def state(
            online: bool,
            value: MemoryHostState,
            *,
            content_sha256: str | None = None,
            error: str | None = None,
        ) -> MemoryHost:
            return MemoryHost(
                host_id=host.host_id,
                host_name=host.name,
                online=online,
                state=value,
                content_sha256=content_sha256,
                error=error,
            )

        conn, error = reachable(host)
        if conn is None:
            if error == _OFFLINE:
                return state(False, "offline", error=error), None
            return state(True, "needs_update", error=error), None
        try:
            payload = await call(conn, "memory.read", {})
        except HostFsError as exc:
            return state(True, "error", error=exc.message), None
        except HostFsUnavailableError as exc:
            return state(True, "error", error=str(exc)), None
        sha = payload.get("content_sha256")
        content = payload.get("content")
        if not payload.get("exists") or not isinstance(sha, str) or not isinstance(content, str):
            return state(True, "missing"), None
        return state(True, "present", content_sha256=sha), content

    @router.get("/memory-sync", response_model=MemorySyncState)
    async def get_memory(request: Request) -> MemorySyncState:
        """Read ``~/AGENTS.md`` from each of the caller's online hosts, grouped by content."""
        hosts = await owned_hosts(request)
        read = await asyncio.gather(*(read_host(host) for host in hosts))
        variants: dict[str, MemoryVariant] = {}
        for host_state, content in read:
            if host_state.content_sha256 is None or content is None:
                continue
            variant = variants.setdefault(
                host_state.content_sha256,
                MemoryVariant(
                    content_sha256=host_state.content_sha256, content=content, host_ids=[]
                ),
            )
            variant.host_ids.append(host_state.host_id)
        ordered = sorted(
            variants.values(), key=lambda item: (-len(item.host_ids), item.content_sha256)
        )
        return MemorySyncState(
            file=MEMORY_FILE, variants=ordered, hosts=[host_state for host_state, _ in read]
        )

    @router.post("/memory-sync/write", response_model=MemoryWriteResult)
    async def write_memory(request: Request, body: MemoryWriteRequest) -> MemoryWriteResult:
        """Write new ``~/AGENTS.md`` text to the target hosts.

        A host takes the text only while its file still has ``expected_sha256``;
        otherwise its result is ``conflict`` and its file stays as it is.
        """
        owned = {host.host_id: host for host in await owned_hosts(request)}

        async def write_host(target: MemoryWriteTarget) -> MemoryWriteHostResult:
            def failed(error: str) -> MemoryWriteHostResult:
                return MemoryWriteHostResult(host_id=target.host_id, status="failed", error=error)

            host = owned.get(target.host_id)
            if host is None:
                return failed("Host not found.")
            conn, error = reachable(host)
            if conn is None:
                return failed(error or _OFFLINE)
            params = {"content": body.content, "expected_sha256": target.expected_sha256}
            try:
                payload = await call(conn, "memory.write", params, write=True)
            except HostFsError as exc:
                if exc.status == 409:
                    return MemoryWriteHostResult(
                        host_id=target.host_id, status="conflict", error=exc.message
                    )
                return failed(exc.message)
            except HostFsUnavailableError as exc:
                return failed(str(exc))
            sha = payload.get("content_sha256")
            return MemoryWriteHostResult(
                host_id=target.host_id,
                status="done",
                content_sha256=sha if isinstance(sha, str) else None,
            )

        targets = list({target.host_id: target for target in body.targets}.values())
        results = await asyncio.gather(*(write_host(target) for target in targets))
        return MemoryWriteResult(results=list(results))

    return router
