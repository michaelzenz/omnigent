"""Tests for the skill sync routes (``/v1/skill-sync``).

Each fake host runs the real host skill operations in its own home folder.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request

from omnigent.harness_availability import HarnessAvailability
from omnigent.host.frames import CAP_SKILL_SYNC, HostHelloFrame
from omnigent.host.skill_ops import SKILL_READ_OPS, SKILL_WRITE_OPS, handle_skill_fs_op
from omnigent.server.host_registry import HostConnection, HostRegistry
from omnigent.server.routes import skill_sync
from omnigent.server.routes._host_filesystem import HostFsError
from omnigent.stores.host_store import HostStore

OWNER = {"x-test-user": "owner"}
ROUTE = "/v1/skill-sync"
ALL_HARNESSES: dict[str, HarnessAvailability] = {
    "claude-native": True,
    "codex-native": True,
    "cursor-native": True,
}
CLAUDE_ONLY: dict[str, HarnessAvailability] = {"claude-native": True}
LAPTOP, DESKTOP, OLD, GONE, BOBS = (uuid.uuid4().hex for _ in range(5))

AddHost = Callable[..., Path]


class _Auth:
    def get_user_id(self, request: Request) -> str | None:
        return request.headers.get("x-test-user")


def _skill(home: Path, root: str, name: str, body: str) -> Path:
    folder = home / root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo\n---\n\n{body}\n", encoding="utf-8"
    )
    return folder


@pytest.fixture
def registry() -> HostRegistry:
    return HostRegistry()


@pytest.fixture
def client(db_uri: str, registry: HostRegistry) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(
        skill_sync.create_skill_sync_router(registry, HostStore(db_uri), auth_provider=_Auth()),
        prefix="/v1",
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def add_host(
    db_uri: str, registry: HostRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AddHost:
    """Register a host whose skill operations run in its own temporary home folder."""
    store = HostStore(db_uri)
    homes: dict[str, Path] = {}

    def fake(allowed: frozenset[str]) -> Callable[..., Any]:
        async def run(*, host_conn: HostConnection, op: str, params: dict[str, Any], **_: Any):
            assert op in allowed
            home = homes[host_conn.host_id]
            monkeypatch.setenv("HOME", str(home))
            monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(home.parent / "config"))
            try:
                return handle_skill_fs_op(op, params)
            except FileNotFoundError as exc:
                raise HostFsError(404, "not_found", str(exc)) from exc
            except FileExistsError as exc:
                raise HostFsError(409, "conflict", str(exc)) from exc
            except ValueError as exc:
                raise HostFsError(400, "invalid_request", str(exc)) from exc

        return run

    monkeypatch.setattr(skill_sync, "read_workspace_from_host", fake(SKILL_READ_OPS))
    monkeypatch.setattr(skill_sync, "write_workspace_from_host", fake(SKILL_WRITE_OPS))

    def add(
        host_id: str,
        name: str,
        harnesses: dict[str, HarnessAvailability],
        *,
        owner: str = "owner",
        connected: bool = True,
        capable: bool = True,
    ) -> Path:
        store.upsert_on_connect(host_id, name, owner, configured_harnesses=harnesses)
        if connected:
            registry.register(
                host_id,
                AsyncMock(),
                HostHelloFrame(
                    version="test",
                    frame_protocol_version=1,
                    name=name,
                    capabilities=[CAP_SKILL_SYNC] if capable else [],
                ),
                owner=owner,
            )
        homes[host_id] = tmp_path / host_id / "home"
        homes[host_id].mkdir(parents=True)
        return homes[host_id]

    return add


async def _skills(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(f"{ROUTE}/skills", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_skills_show_each_variant_and_each_harness_state(
    client: httpx.AsyncClient, add_host: AddHost
) -> None:
    laptop = add_host(LAPTOP, "laptop", ALL_HARNESSES)
    desktop = add_host(DESKTOP, "desktop", CLAUDE_ONLY)
    add_host(OLD, "old", ALL_HARNESSES, capable=False)
    add_host(GONE, "gone", ALL_HARNESSES, connected=False)
    add_host(BOBS, "bobs", ALL_HARNESSES, owner="bob")
    _skill(laptop, ".claude/skills", "pdf", "A")
    _skill(laptop, ".cursor/skills", "pdf", "A")
    _skill(desktop, ".claude/skills", "pdf", "B")

    [pdf] = await _skills(client)
    roots = (await client.get(f"{ROUTE}/roots", headers=OWNER)).json()["data"]

    states = {
        host["host_name"]: {row["harness"]: row["state"] for row in host["harnesses"]}
        for host in pdf["hosts"]
    }
    assert [len(variant["occurrences"]) for variant in pdf["variants"]] == [2, 1]
    assert pdf["sync_status"] == "not_synced"
    assert states == {
        "laptop": {"claude": "present", "codex": "missing", "cursor": "present"},
        "desktop": {"claude": "present", "codex": "unavailable", "cursor": "unavailable"},
        "old": {"claude": "not_reported", "codex": "not_reported", "cursor": "not_reported"},
        "gone": {"claude": "offline", "codex": "offline", "cursor": "offline"},
    }
    assert {host["host_name"]: host["error"] for host in roots} == {
        "laptop": None,
        "desktop": None,
        "old": "Update the host to sync skills.",
        "gone": "Host is offline.",
    }


async def test_sync_to_all_gives_every_participating_harness_the_same_copy(
    client: httpx.AsyncClient, add_host: AddHost
) -> None:
    laptop = add_host(LAPTOP, "laptop", ALL_HARNESSES)
    desktop = add_host(DESKTOP, "desktop", CLAUDE_ONLY)
    _skill(laptop, ".claude/skills", "pdf", "A")
    _skill(desktop, ".claude/skills", "pdf", "B")

    synced = await client.post(
        f"{ROUTE}/skills/pdf/sync",
        json={"source_host_id": LAPTOP, "source_harness": "claude"},
        headers=OWNER,
    )
    [pdf] = await _skills(client)

    assert {(row["host_id"], row["harness"]): row["status"] for row in synced.json()["hosts"]} == {
        (LAPTOP, "claude"): "done",
        (LAPTOP, "codex"): "done",
        (LAPTOP, "cursor"): "done",
        (DESKTOP, "claude"): "done",
        (DESKTOP, "codex"): "unavailable",
        (DESKTOP, "cursor"): "unavailable",
    }
    # Only harnesses that a host lacks keep the skill from "synced".
    assert (pdf["sync_status"], len(pdf["variants"])) == ("partial", 1)
    assert "A" in (desktop / ".claude/skills/pdf/SKILL.md").read_text(encoding="utf-8")


async def test_a_variant_is_read_and_edited_and_a_skill_is_created_and_deleted(
    client: httpx.AsyncClient, add_host: AddHost
) -> None:
    laptop = add_host(LAPTOP, "laptop", CLAUDE_ONLY)
    (_skill(laptop, ".claude/skills", "pdf", "A") / "image.bin").write_bytes(b"\xff\xfe")
    [pdf] = await _skills(client)
    variant = pdf["variants"][0]["content_sha256"]
    edited = "---\nname: pdf\ndescription: Demo\n---\n\nedited\n"
    notes = {"files": {"SKILL.md": "---\nname: notes\ndescription: Notes\n---\n\nhi\n"}}

    tree = await client.get(
        f"{ROUTE}/skills/pdf/tree", params={"host_id": LAPTOP, "harness": "claude"}, headers=OWNER
    )
    saved = await client.put(
        f"{ROUTE}/skills/pdf/variants/{variant}/files",
        json={"files": {"SKILL.md": edited}},
        headers=OWNER,
    )
    stale = await client.put(
        f"{ROUTE}/skills/pdf/variants/{variant}/files",
        json={"files": {"SKILL.md": edited}},
        headers=OWNER,
    )
    created = await client.post(f"{ROUTE}/skills/notes/files", json=notes, headers=OWNER)
    duplicate = await client.post(f"{ROUTE}/skills/notes/files", json=notes, headers=OWNER)
    bad_name = await client.post(f"{ROUTE}/skills/bad%20name/files", json=notes, headers=OWNER)
    deleted = await client.delete(f"{ROUTE}/skills/notes", headers=OWNER)

    assert [(file["path"], file["binary"]) for file in tree.json()["files"]] == [
        ("SKILL.md", False),
        ("image.bin", True),
    ]
    assert saved.json()["content_sha256"] not in (None, variant)
    assert (laptop / ".claude/skills/pdf/SKILL.md").read_text(encoding="utf-8") == edited
    assert stale.status_code == 404
    assert {(row["harness"], row["status"]) for row in created.json()["results"]} == {
        ("claude", "done"),
        ("omnigent", "done"),
    }
    assert (duplicate.status_code, bad_name.status_code) == (409, 422)
    assert {(row["harness"], row["status"]) for row in deleted.json()["hosts"]} == {
        ("claude", "done"),
        ("omnigent", "done"),
    }
    assert not (laptop / ".claude/skills/notes").exists()
    assert not (laptop / ".omnigent/skills/notes").exists()


async def test_a_harness_setting_reaches_each_online_host_and_hosts_stay_the_owners(
    client: httpx.AsyncClient, add_host: AddHost
) -> None:
    laptop = add_host(LAPTOP, "laptop", ALL_HARNESSES)
    add_host(BOBS, "bobs", ALL_HARNESSES, owner="bob")
    _skill(laptop, ".claude/skills", "pdf", "A")
    _skill(laptop, ".cursor/skills", "pdf", "A")

    updated = await client.put(f"{ROUTE}/roots/cursor", json={"enabled": False}, headers=OWNER)
    roots = (await client.get(f"{ROUTE}/roots", headers=OWNER)).json()["data"]
    [pdf] = await _skills(client)
    bobs_copy = await client.get(
        f"{ROUTE}/skills/pdf/tree", params={"host_id": BOBS, "harness": "claude"}, headers=OWNER
    )
    unknown = await client.put(f"{ROUTE}/roots/gemini", json={"enabled": False}, headers=OWNER)

    assert [row["status"] for row in updated.json()["hosts"]] == ["done"]
    assert [(host["host_name"], host["sync_harnesses"]["cursor"]) for host in roots] == [
        ("laptop", False)
    ]
    # An ignored harness whose copy matches the synced one is not shown.
    assert [row["harness"] for row in pdf["hosts"][0]["harnesses"]] == ["claude", "codex"]
    assert (bobs_copy.status_code, unknown.status_code) == (403, 404)
