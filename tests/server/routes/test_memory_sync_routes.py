"""Tests for the memory sync routes (``/v1/memory-sync``).

Each fake host runs the real host memory operations in its own home folder.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request

from omnigent.host.frames import CAP_MEMORY_SYNC, HostHelloFrame
from omnigent.host.memory_ops import MEMORY_READ_OPS, MEMORY_WRITE_OPS, handle_memory_fs_op
from omnigent.server.host_registry import HostConnection, HostRegistry
from omnigent.server.routes import memory_sync
from omnigent.server.routes._host_filesystem import HostFsError
from omnigent.stores.host_store import HostStore

OWNER = {"x-test-user": "owner"}
ROUTE = "/v1/memory-sync"
LAPTOP, DESKTOP, REMOTE, EMPTY, OLD, GONE, BOBS = (uuid.uuid4().hex for _ in range(7))

AddHost = Callable[..., Path]


class _Auth:
    def get_user_id(self, request: Request) -> str | None:
        return request.headers.get("x-test-user")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def registry() -> HostRegistry:
    return HostRegistry()


@pytest.fixture
def client(db_uri: str, registry: HostRegistry) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(
        memory_sync.create_memory_sync_router(registry, HostStore(db_uri), auth_provider=_Auth()),
        prefix="/v1",
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def add_host(
    db_uri: str, registry: HostRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AddHost:
    """Register a host whose memory operations run in its own temporary home folder."""
    store = HostStore(db_uri)
    homes: dict[str, Path] = {}

    def fake(allowed: frozenset[str]) -> Callable[..., Any]:
        async def run(*, host_conn: HostConnection, op: str, params: dict[str, Any], **_: Any):
            assert op in allowed
            try:
                return handle_memory_fs_op(op, params, home=homes[host_conn.host_id])
            except FileExistsError as exc:
                raise HostFsError(409, "conflict", str(exc)) from exc
            except ValueError as exc:
                raise HostFsError(400, "invalid_request", str(exc)) from exc

        return run

    monkeypatch.setattr(memory_sync, "read_workspace_from_host", fake(MEMORY_READ_OPS))
    monkeypatch.setattr(memory_sync, "write_workspace_from_host", fake(MEMORY_WRITE_OPS))

    def add(
        host_id: str,
        name: str,
        *,
        agents_md: str | None = None,
        owner: str = "owner",
        connected: bool = True,
        capable: bool = True,
    ) -> Path:
        store.upsert_on_connect(host_id, name, owner, configured_harnesses={})
        if connected:
            registry.register(
                host_id,
                AsyncMock(),
                HostHelloFrame(
                    version="test",
                    frame_protocol_version=1,
                    name=name,
                    capabilities=[CAP_MEMORY_SYNC] if capable else [],
                ),
                owner=owner,
            )
        homes[host_id] = tmp_path / host_id / "home"
        homes[host_id].mkdir(parents=True)
        if agents_md is not None:
            (homes[host_id] / "AGENTS.md").write_text(agents_md, encoding="utf-8")
        return homes[host_id]

    return add


async def test_hosts_with_the_same_agents_md_form_one_variant(
    client: httpx.AsyncClient, add_host: AddHost
) -> None:
    add_host(LAPTOP, "laptop", agents_md="shared\n")
    add_host(DESKTOP, "desktop", agents_md="shared\n")
    add_host(REMOTE, "remote", agents_md="remote rules\n")
    add_host(EMPTY, "empty")
    add_host(OLD, "old", agents_md="old\n", capable=False)
    add_host(GONE, "gone", agents_md="gone\n", connected=False)
    add_host(BOBS, "bobs", agents_md="bob's\n", owner="bob")

    body = (await client.get(ROUTE, headers=OWNER)).json()

    assert [(variant["content"], sorted(variant["host_ids"])) for variant in body["variants"]] == [
        ("shared\n", sorted([LAPTOP, DESKTOP])),
        ("remote rules\n", [REMOTE]),
    ]
    assert {host["host_id"]: host["state"] for host in body["hosts"]} == {
        LAPTOP: "present",
        DESKTOP: "present",
        REMOTE: "present",
        EMPTY: "missing",
        OLD: "needs_update",
        GONE: "offline",
    }


async def test_a_write_reaches_only_the_callers_hosts_whose_file_is_unchanged(
    client: httpx.AsyncClient, add_host: AddHost
) -> None:
    laptop = add_host(LAPTOP, "laptop", agents_md="shared\n")
    desktop = add_host(DESKTOP, "desktop", agents_md="shared\n")
    empty = add_host(EMPTY, "empty")
    add_host(OLD, "old", agents_md="shared\n", capable=False)
    add_host(GONE, "gone", agents_md="shared\n", connected=False)
    bobs = add_host(BOBS, "bobs", agents_md="shared\n", owner="bob")
    (desktop / "AGENTS.md").write_text("edited on the desktop\n", encoding="utf-8")
    shared = _sha("shared\n")

    response = await client.post(
        f"{ROUTE}/write",
        headers=OWNER,
        json={
            "content": "new rules\n",
            "targets": [
                {"host_id": host_id, "expected_sha256": shared}
                for host_id in (LAPTOP, DESKTOP, OLD, GONE, BOBS)
            ]
            + [{"host_id": EMPTY, "expected_sha256": None}],
        },
    )

    results = {
        result["host_id"]: (result["status"], result["content_sha256"])
        for result in response.json()["results"]
    }
    assert results == {
        LAPTOP: ("done", _sha("new rules\n")),
        DESKTOP: ("conflict", None),
        OLD: ("failed", None),
        GONE: ("failed", None),
        BOBS: ("failed", None),
        EMPTY: ("done", _sha("new rules\n")),
    }
    assert [
        (home / "AGENTS.md").read_text(encoding="utf-8") for home in (laptop, desktop, empty, bobs)
    ] == ["new rules\n", "edited on the desktop\n", "new rules\n", "shared\n"]
