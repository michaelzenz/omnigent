"""Skill sync across the caller's hosts (``/v1/skill-sync``).

Each request asks every online host of the caller that supports skill sync
for its home skill folders, so the server keeps no skill state. Copies of a
skill with the same content hash form one variant. Changes run as the
``skill.*`` host file operations. A host is reachable only from the server
replica it is connected to.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from omnigent.host.frames import CAP_SKILL_SYNC
from omnigent.server.auth import AuthProvider
from omnigent.server.host_registry import HostConnection, HostRegistry
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._host_filesystem import (
    HostFsError,
    HostFsUnavailableError,
    read_workspace_from_host,
    write_workspace_from_host,
)
from omnigent.server.routes._host_launch import resolve_host_owner
from omnigent.stores.host_store import Host, HostStore, host_is_live

_SKILL_HARNESSES = ("claude", "codex", "cursor")
# The configured_harnesses keys that show a harness can run on the host.
_HARNESS_READINESS_KEYS: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "claude-sdk", "claude-native", "native-claude"),
    "codex": ("codex", "codex-native", "native-codex"),
    "cursor": ("cursor", "cursor-native", "native-cursor"),
}
_NATIVE_SKILL_PATHS = {
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "cursor": ".cursor/skills",
}
# Every harness's generic skill lookup reads this folder, so a new skill also goes there.
_OMNIGENT_HARNESS = "omnigent"
_OMNIGENT_SKILL_PATH = ".omnigent/skills"
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_NEW_SKILL_FILES = 100
_MAX_NEW_SKILL_CHARS = 1_000_000
_OFFLINE = "Host is offline."
_NEEDS_UPDATE = "Update the host to sync skills."

SkillSyncState = Literal[
    "present", "missing", "unavailable", "offline", "not_reported", "ignored", "ignored_variant"
]


class SkillSyncOccurrence(BaseModel):
    """One copy of a skill: a harness's folder on one host."""

    name: str
    description: str
    harness: str
    rel_home_path: str
    content_sha256: str
    host_id: str
    host_name: str
    online: bool


class SkillSyncVariant(BaseModel):
    """The copies of a skill with the same content; ``active_count`` counts online copies."""

    content_sha256: str
    active_count: int
    occurrences: list[SkillSyncOccurrence]


class SkillSyncHarnessState(BaseModel):
    """Where a skill stands for one harness on one host.

    ``enabled`` is the host's sync setting for the harness, null when unknown.
    ``ignored_variant`` is a harness that does not take part in sync but
    holds a copy that differs from the synced ones.
    """

    harness: str
    installed: bool
    enabled: bool | None
    state: SkillSyncState
    occurrence: SkillSyncOccurrence | None


class SkillSyncHostState(BaseModel):
    """A skill's state on one host; ``reported`` is false when its inventory is unknown."""

    host_id: str
    host_name: str
    online: bool
    reported: bool
    harnesses: list[SkillSyncHarnessState]


class SkillSyncSkill(BaseModel):
    """A skill by name across the caller's hosts.

    It is ``synced`` when every participating harness on every host has the
    same copy; ``partial`` when the only gaps are harnesses that a host does
    not have.
    """

    name: str
    description: str
    synced: bool
    sync_status: Literal["synced", "partial", "not_synced"]
    variants: list[SkillSyncVariant]
    hosts: list[SkillSyncHostState]


class SkillSyncSkillList(BaseModel):
    """The caller's skills by name, sorted."""

    object: Literal["list"] = "list"
    data: list[SkillSyncSkill]


class SkillSyncRoot(BaseModel):
    """A home folder that a harness searches for skills."""

    harness: str
    rel_home_path: str


class SkillSyncHostRoots(BaseModel):
    """A host's skill folders and sync settings; ``error`` says why they are unknown."""

    host_id: str
    host_name: str
    online: bool
    roots: list[SkillSyncRoot]
    sync_harnesses: dict[str, bool] | None
    installed_harnesses: dict[str, bool]
    error: str | None


class SkillSyncHostRootsList(BaseModel):
    """The skill folders and sync settings of each of the caller's hosts."""

    object: Literal["list"] = "list"
    data: list[SkillSyncHostRoots]


class SkillSyncHarnessSettingRequest(BaseModel):
    """Whether a harness takes part in skill sync."""

    enabled: bool

    model_config = ConfigDict(extra="forbid")


class SkillSyncHostResult(BaseModel):
    """The outcome of a change on one host, and harness when it applies."""

    host_id: str
    harness: str | None = None
    status: str
    error: str | None = None


class SkillSyncHarnessSettingResult(BaseModel):
    """The outcome of a harness setting change on each host."""

    harness: str
    enabled: bool
    hosts: list[SkillSyncHostResult]


class SkillSyncContent(BaseModel):
    """The ``SKILL.md`` of one copy."""

    host_id: str
    name: str
    content: str


class SkillSyncTreeFile(BaseModel):
    """One file of a copy; a binary file shows its hash instead of its bytes."""

    path: str
    content: str
    binary: bool


class SkillSyncTree(BaseModel):
    """Every file of one copy, ``SKILL.md`` first."""

    host_id: str
    name: str
    rel_home_path: str
    files: list[SkillSyncTreeFile]


class SkillSyncFilesRequest(BaseModel):
    """Text files by path inside the skill folder, e.g. ``{"SKILL.md": "..."}``."""

    files: dict[str, str]

    model_config = ConfigDict(extra="forbid")


class SkillSyncWriteResult(BaseModel):
    """The outcome on each copy; ``content_sha256`` is the new hash when all copies agree."""

    object: Literal["skill_sync.write_result"] = "skill_sync.write_result"
    results: list[SkillSyncHostResult]
    content_sha256: str | None


class SkillSyncRequest(BaseModel):
    """Send one copy of a skill to the other hosts; no targets means every host."""

    source_host_id: str
    source_harness: str
    target_host_ids: list[str] | None = None

    model_config = ConfigDict(extra="forbid")


class SkillSyncResult(BaseModel):
    """The outcome of a sync or a delete on each host and harness."""

    object: Literal["skill_sync.result"] = "skill_sync.result"
    name: str
    hosts: list[SkillSyncHostResult]


@dataclass
class _HostSkills:
    """What one host reported for this request.

    ``online`` is a live host connected to this replica; ``conn`` is set only
    when it can also run skill sync.
    """

    host: Host
    conn: HostConnection | None
    online: bool
    inventory: list[dict[str, str]] | None = None
    roots: list[dict[str, str]] | None = None
    sync_harnesses: dict[str, bool] | None = None
    error: str | None = None

    def entries(self, name: str) -> list[dict[str, str]]:
        return [entry for entry in self.inventory or [] if entry["name"] == name]


def create_skill_sync_router(
    host_registry: HostRegistry,
    host_store: HostStore,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the routes that sync home skill folders across the caller's hosts.

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
        try:
            return await send(
                host_registry=host_registry,
                host_conn=conn,
                op=op,
                workspace="",
                session_id="",
                params=params,
            )
        except HostFsError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        except HostFsUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    def reachable(host: Host) -> tuple[HostConnection | None, str | None]:
        conn = host_registry.get(host.host_id)
        if conn is None or not host_is_live(host):
            return None, _OFFLINE
        if CAP_SKILL_SYNC not in conn.hello.capabilities:
            return None, _NEEDS_UPDATE
        return conn, None

    async def load(host: Host) -> _HostSkills:
        conn, error = reachable(host)
        if conn is None:
            return _HostSkills(host, None, online=error != _OFFLINE, error=error)
        try:
            inventory, roots = await asyncio.gather(
                call(conn, "skill.inventory", {}), call(conn, "skill.roots", {})
            )
        except HTTPException as exc:
            return _HostSkills(host, conn, online=True, error=str(exc.detail))
        return _HostSkills(
            host,
            conn,
            online=True,
            inventory=_inventory(inventory),
            roots=_roots(roots.get("roots")),
            sync_harnesses=_sync_harnesses(roots.get("sync_harnesses")),
        )

    async def load_all(request: Request) -> list[_HostSkills]:
        user_id = require_user(request, auth_provider)
        # With auth disabled, the single-user server's hosts belong to "local".
        hosts = await asyncio.to_thread(host_store.list_hosts, user_id or "local")
        return list(await asyncio.gather(*(load(host) for host in hosts)))

    async def owned_copy(
        request: Request, host_id: str, name: str, harness: str
    ) -> tuple[HostConnection, dict[str, str]]:
        user_id = require_user(request, auth_provider)
        host = await asyncio.to_thread(
            resolve_host_owner, user_id=user_id, host_id=host_id, host_store=host_store
        )
        conn, error = reachable(host)
        if conn is None:
            raise HTTPException(status_code=409, detail=error)
        inventory = _inventory(await call(conn, "skill.inventory", {}))
        entry = next(
            (item for item in inventory if item["name"] == name and item["harness"] == harness),
            None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail="Skill not found on host.")
        return conn, entry

    async def change(
        host_id: str,
        harness: str | None,
        conn: HostConnection,
        op: str,
        params: dict[str, Any],
        *,
        missing_ok: bool = False,
    ) -> tuple[SkillSyncHostResult, dict[str, Any] | None]:
        done = SkillSyncHostResult(host_id=host_id, harness=harness, status="done")
        try:
            payload = await call(conn, op, params, write=True)
        except HTTPException as exc:
            if missing_ok and exc.status_code == 404:
                return done, None
            failure = SkillSyncHostResult(
                host_id=host_id, harness=harness, status="failed", error=str(exc.detail)
            )
            return failure, None
        return done, payload

    @router.get("/skill-sync/skills", response_model=SkillSyncSkillList)
    async def list_skills(request: Request) -> SkillSyncSkillList:
        """List the caller's home skills by name, with their copies on each online host."""
        loaded = await load_all(request)
        names = sorted({entry["name"] for item in loaded for entry in item.inventory or []})
        return SkillSyncSkillList(data=[_aggregate(name, loaded) for name in names])

    @router.get("/skill-sync/roots", response_model=SkillSyncHostRootsList)
    async def list_roots(request: Request) -> SkillSyncHostRootsList:
        """List each host's skill folders, sync settings and installed harnesses."""
        loaded = await load_all(request)
        return SkillSyncHostRootsList(
            data=[
                SkillSyncHostRoots(
                    host_id=item.host.host_id,
                    host_name=item.host.name,
                    online=item.online,
                    roots=[
                        SkillSyncRoot(harness=root["harness"], rel_home_path=root["rel_home_path"])
                        for root in item.roots or []
                    ],
                    sync_harnesses=item.sync_harnesses,
                    installed_harnesses={
                        harness: _installed(item.host.configured_harnesses, harness)
                        for harness in _SKILL_HARNESSES
                    },
                    error=item.error,
                )
                for item in loaded
            ]
        )

    @router.put("/skill-sync/roots/{harness}", response_model=SkillSyncHarnessSettingResult)
    async def update_harness_setting(
        request: Request, harness: str, body: SkillSyncHarnessSettingRequest
    ) -> SkillSyncHarnessSettingResult:
        """Make a harness take part in skill sync, or not, on every online host."""
        if harness not in _SKILL_HARNESSES:
            raise HTTPException(status_code=404, detail="Harness not found.")
        user_id = require_user(request, auth_provider)
        hosts = await asyncio.to_thread(host_store.list_hosts, user_id or "local")
        results: list[SkillSyncHostResult] = []
        for host in hosts:
            conn, error = reachable(host)
            if conn is None:
                results.append(
                    SkillSyncHostResult(host_id=host.host_id, status="skipped", error=error)
                )
                continue
            result, _ = await change(
                host.host_id,
                harness,
                conn,
                "skill.settings",
                {"harness": harness, "enabled": body.enabled},
            )
            results.append(result)
        return SkillSyncHarnessSettingResult(harness=harness, enabled=body.enabled, hosts=results)

    @router.get("/skill-sync/skills/{name}/content", response_model=SkillSyncContent)
    async def read_skill(
        request: Request, name: str, host_id: str = Query(...), harness: str = Query(...)
    ) -> SkillSyncContent:
        """Read the ``SKILL.md`` of one copy."""
        conn, entry = await owned_copy(request, host_id, name, harness)
        payload = await call(
            conn, "skill.read", {"name": name, "rel_home_path": entry["rel_home_path"]}
        )
        content = payload.get("content")
        if not isinstance(content, str):
            raise HTTPException(status_code=502, detail="Host returned invalid skill content.")
        return SkillSyncContent(host_id=host_id, name=name, content=content)

    @router.get("/skill-sync/skills/{name}/tree", response_model=SkillSyncTree)
    async def read_skill_tree(
        request: Request, name: str, host_id: str = Query(...), harness: str = Query(...)
    ) -> SkillSyncTree:
        """Read every file of one copy."""
        conn, entry = await owned_copy(request, host_id, name, harness)
        payload = await call(
            conn, "skill.export", {"name": name, "rel_home_path": entry["rel_home_path"]}
        )
        return SkillSyncTree(
            host_id=host_id,
            name=name,
            rel_home_path=entry["rel_home_path"],
            files=_tree_files(payload.get("files")),
        )

    @router.put(
        "/skill-sync/skills/{name}/variants/{content_sha256}/files",
        response_model=SkillSyncWriteResult,
    )
    async def write_variant_files(
        request: Request, name: str, content_sha256: str, body: SkillSyncFilesRequest
    ) -> SkillSyncWriteResult:
        """Write edited text files to every copy of one variant.

        A copy that another copy's write already changed, such as a symlinked
        folder, counts as saved.
        """
        loaded = await load_all(request)
        copies = [
            (item, conn, entry)
            for item in loaded
            if (conn := item.conn) is not None
            for entry in item.entries(name)
            if entry["content_sha256"] == content_sha256
        ]
        if not copies:
            raise HTTPException(status_code=404, detail="Skill variant not found.")
        latest = {item.host.host_id: item.inventory or [] for item, _, _ in copies}
        new_hashes: set[str] = set()
        results: list[SkillSyncHostResult] = []
        for item, conn, entry in copies:
            host_id = item.host.host_id
            current = _same_copy(latest[host_id], entry)
            if current is not None and current["content_sha256"] in new_hashes:
                results.append(
                    SkillSyncHostResult(host_id=host_id, harness=entry["harness"], status="done")
                )
                continue
            result, payload = await change(
                host_id,
                entry["harness"],
                conn,
                "skill.files.write",
                {
                    "name": name,
                    "rel_home_path": entry["rel_home_path"],
                    "files": body.files,
                    "expected_content_sha256": content_sha256,
                },
            )
            results.append(result)
            if payload is not None:
                latest[host_id] = _inventory(payload)
                updated = _same_copy(latest[host_id], entry)
                if updated is not None:
                    new_hashes.add(updated["content_sha256"])
        return SkillSyncWriteResult(
            results=results, content_sha256=_single(new_hashes) if not _failed(results) else None
        )

    @router.post("/skill-sync/skills/{name}/files", response_model=SkillSyncWriteResult)
    async def create_skill(
        request: Request, name: str, body: SkillSyncFilesRequest
    ) -> SkillSyncWriteResult:
        """Create a skill on every online host.

        It goes into each participating harness folder and the Omnigent folder.
        """
        _validate_new_skill(name, body.files)
        loaded = await load_all(request)
        if any(item.entries(name) for item in loaded):
            raise HTTPException(status_code=409, detail="A skill with this name already exists.")
        if not any(item.conn is not None and item.sync_harnesses for item in loaded):
            raise HTTPException(status_code=409, detail="No online host can sync skills.")
        files = {
            path: base64.b64encode(content.encode("utf-8")).decode()
            for path, content in body.files.items()
        }
        results: list[SkillSyncHostResult] = []
        new_hashes: set[str] = set()
        for item in loaded:
            host_id = item.host.host_id
            if item.conn is None or item.sync_harnesses is None:
                results.append(
                    SkillSyncHostResult(host_id=host_id, status="skipped", error=item.error)
                )
                continue
            targets = [
                (harness, f"{_NATIVE_SKILL_PATHS[harness]}/{name}")
                for harness in _SKILL_HARNESSES
                if item.sync_harnesses.get(harness, True)
                and _installed(item.host.configured_harnesses, harness)
            ]
            targets.append((_OMNIGENT_HARNESS, f"{_OMNIGENT_SKILL_PATH}/{name}"))
            for harness, rel_home_path in targets:
                result, payload = await change(
                    host_id,
                    harness,
                    item.conn,
                    "skill.import",
                    {
                        "name": name,
                        "rel_home_path": rel_home_path,
                        "files": files,
                        "create_only": True,
                    },
                )
                results.append(result)
                created = _copy_at(_inventory(payload or {}), name, harness, rel_home_path)
                if created is not None:
                    new_hashes.add(created["content_sha256"])
        return SkillSyncWriteResult(results=results, content_sha256=_single(new_hashes))

    @router.post("/skill-sync/skills/{name}/sync", response_model=SkillSyncResult)
    async def sync_skill(request: Request, name: str, body: SkillSyncRequest) -> SkillSyncResult:
        """Copy one host's copy of a skill to every participating harness of the target hosts."""
        source_conn, source = await owned_copy(
            request, body.source_host_id, name, body.source_harness
        )
        exported = await call(
            source_conn, "skill.export", {"name": name, "rel_home_path": source["rel_home_path"]}
        )
        files = exported.get("files")
        if not isinstance(files, dict):
            raise HTTPException(status_code=502, detail="Host returned an invalid skill tree.")
        loaded = await load_all(request)
        targets = set(body.target_host_ids) if body.target_host_ids is not None else None
        results: list[SkillSyncHostResult] = []
        for item in loaded:
            host_id = item.host.host_id
            if targets is not None and host_id not in targets:
                continue
            if item.conn is None or item.sync_harnesses is None:
                results.append(
                    SkillSyncHostResult(host_id=host_id, status="skipped", error=item.error)
                )
                continue
            existing = {entry["harness"]: entry for entry in item.entries(name)}
            for harness in _SKILL_HARNESSES:
                if not item.sync_harnesses.get(harness, True):
                    results.append(
                        SkillSyncHostResult(host_id=host_id, harness=harness, status="ignored")
                    )
                    continue
                if not _installed(item.host.configured_harnesses, harness):
                    results.append(
                        SkillSyncHostResult(host_id=host_id, harness=harness, status="unavailable")
                    )
                    continue
                target = existing.get(harness)
                rel_home_path = (
                    target["rel_home_path"] if target else f"{_NATIVE_SKILL_PATHS[harness]}/{name}"
                )
                result, _ = await change(
                    host_id,
                    harness,
                    item.conn,
                    "skill.import",
                    {"rel_home_path": rel_home_path, "files": files},
                )
                results.append(result)
            if omnigent := existing.get(_OMNIGENT_HARNESS):
                result, _ = await change(
                    host_id,
                    _OMNIGENT_HARNESS,
                    item.conn,
                    "skill.import",
                    {"rel_home_path": omnigent["rel_home_path"], "files": files},
                )
                results.append(result)
        return SkillSyncResult(name=name, hosts=results)

    @router.delete("/skill-sync/skills/{name}", response_model=SkillSyncResult)
    async def delete_skill(request: Request, name: str) -> SkillSyncResult:
        """Delete every copy of a skill on every online host."""
        loaded = await load_all(request)
        results: list[SkillSyncHostResult] = []
        for item in loaded:
            host_id = item.host.host_id
            if item.conn is None:
                results.append(
                    SkillSyncHostResult(host_id=host_id, status="skipped", error=item.error)
                )
                continue
            for entry in item.entries(name):
                # A symlinked copy can go with the folder it points to.
                result, _ = await change(
                    host_id,
                    entry["harness"],
                    item.conn,
                    "skill.delete",
                    {"name": name, "rel_home_path": entry["rel_home_path"]},
                    missing_ok=True,
                )
                results.append(result)
        return SkillSyncResult(name=name, hosts=results)

    return router


def _aggregate(name: str, loaded: list[_HostSkills]) -> SkillSyncSkill:
    """Describe one skill: its variants, and its state for each harness on each host."""
    occurrences = [
        SkillSyncOccurrence(
            name=entry["name"],
            description=entry["description"],
            harness=entry["harness"],
            rel_home_path=entry["rel_home_path"],
            content_sha256=entry["content_sha256"],
            host_id=item.host.host_id,
            host_name=item.host.name,
            online=item.online,
        )
        for item in loaded
        for entry in item.entries(name)
    ]
    by_hash: dict[str, list[SkillSyncOccurrence]] = {}
    for occurrence in occurrences:
        by_hash.setdefault(occurrence.content_sha256, []).append(occurrence)
    variants = sorted(
        (
            SkillSyncVariant(
                content_sha256=content_sha256,
                active_count=sum(copy.online for copy in copies),
                occurrences=sorted(
                    copies,
                    key=lambda copy: (
                        not copy.online,
                        copy.host_name,
                        copy.harness,
                        copy.rel_home_path,
                    ),
                ),
            )
            for content_sha256, copies in by_hash.items()
        ),
        key=lambda variant: (-variant.active_count, variant.content_sha256),
    )
    has_failure = not loaded
    has_unavailable = False
    participating: set[str] = set()
    hosts: list[SkillSyncHostState] = []
    for item in loaded:
        mine = {copy.harness: copy for copy in occurrences if copy.host_id == item.host.host_id}
        rows: list[SkillSyncHarnessState] = []
        for harness in _SKILL_HARNESSES:
            enabled = item.sync_harnesses.get(harness) if item.sync_harnesses else None
            installed = _installed(item.host.configured_harnesses, harness)
            copy = mine.get(harness)
            state: SkillSyncState
            if not item.online:
                state = "offline"
            elif item.inventory is None or enabled is None:
                state = "not_reported"
            elif not enabled:
                state = "ignored"
            elif not installed:
                state = "unavailable"
            elif copy is None:
                state = "missing"
            else:
                state = "present"
                participating.add(copy.content_sha256)
            has_failure = has_failure or state in {"offline", "not_reported", "missing"}
            has_unavailable = has_unavailable or state == "unavailable"
            rows.append(
                SkillSyncHarnessState(
                    harness=harness,
                    installed=installed,
                    enabled=enabled,
                    state=state,
                    occurrence=copy,
                )
            )
        if omnigent := mine.get(_OMNIGENT_HARNESS):
            participating.add(omnigent.content_sha256)
            rows.append(
                SkillSyncHarnessState(
                    harness=_OMNIGENT_HARNESS,
                    installed=True,
                    enabled=True,
                    state="present",
                    occurrence=omnigent,
                )
            )
        hosts.append(
            SkillSyncHostState(
                host_id=item.host.host_id,
                host_name=item.host.name,
                online=item.online,
                reported=item.inventory is not None,
                harnesses=rows,
            )
        )
    for host in hosts:
        visible: list[SkillSyncHarnessState] = []
        for row in host.harnesses:
            if row.state == "ignored":
                # An ignored harness shows only when its copy differs from the synced ones.
                if row.occurrence is None or row.occurrence.content_sha256 in participating:
                    continue
                row.state = "ignored_variant"
            visible.append(row)
        host.harnesses = visible
    has_failure = has_failure or len(participating) > 1
    status: Literal["synced", "partial", "not_synced"] = (
        "not_synced" if has_failure else "partial" if has_unavailable else "synced"
    )
    return SkillSyncSkill(
        name=name,
        description=next((copy.description for copy in occurrences if copy.description), ""),
        synced=status == "synced",
        sync_status=status,
        variants=variants,
        hosts=hosts,
    )


def _installed(readiness: Mapping[str, object] | None, harness: str) -> bool:
    if readiness is None:
        return False
    return any(
        readiness.get(key) not in (None, False, "binary-missing")
        for key in _HARNESS_READINESS_KEYS[harness]
    )


def _inventory(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return the well-formed entries of a host's ``inventory``; others are dropped."""
    raw = payload.get("inventory")
    keys = ("name", "description", "harness", "rel_home_path", "content_sha256")
    if not isinstance(raw, list):
        return []
    return [
        {key: entry[key] for key in keys}
        for entry in raw
        if isinstance(entry, dict) and all(isinstance(entry.get(key), str) for key in keys)
    ]


def _roots(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    return [
        {"harness": root["harness"], "rel_home_path": root["rel_home_path"]}
        for root in raw
        if isinstance(root, dict)
        and isinstance(root.get("harness"), str)
        and isinstance(root.get("rel_home_path"), str)
    ]


def _sync_harnesses(raw: object) -> dict[str, bool] | None:
    if not isinstance(raw, dict):
        return None
    return {
        harness: value
        for harness, value in raw.items()
        if harness in _SKILL_HARNESSES and isinstance(value, bool)
    }


def _tree_files(raw: object) -> list[SkillSyncTreeFile]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=502, detail="Host returned an invalid skill tree.")
    files: list[SkillSyncTreeFile] = []
    for path, encoded in raw.items():
        if not isinstance(path, str) or not isinstance(encoded, str):
            raise HTTPException(status_code=502, detail="Host returned an invalid skill tree.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=502, detail="Host returned an invalid skill file."
            ) from exc
        try:
            files.append(SkillSyncTreeFile(path=path, content=data.decode("utf-8"), binary=False))
        except UnicodeDecodeError:
            digest = hashlib.sha256(data).hexdigest()
            files.append(
                SkillSyncTreeFile(
                    path=path, content=f"Binary file (sha256: {digest})", binary=True
                )
            )
    return sorted(files, key=lambda file: (file.path.lower() != "skill.md", file.path.lower()))


def _same_copy(inventory: list[dict[str, str]], entry: Mapping[str, str]) -> dict[str, str] | None:
    return _copy_at(inventory, entry["name"], entry["harness"], entry["rel_home_path"])


def _copy_at(
    inventory: list[dict[str, str]], name: str, harness: str, rel_home_path: str
) -> dict[str, str] | None:
    return next(
        (
            item
            for item in inventory
            if item["name"] == name
            and item["harness"] == harness
            and item["rel_home_path"] == rel_home_path
        ),
        None,
    )


def _single(hashes: set[str]) -> str | None:
    return next(iter(hashes)) if len(hashes) == 1 else None


def _failed(results: list[SkillSyncHostResult]) -> bool:
    return any(result.status == "failed" for result in results)


def _validate_new_skill(name: str, files: Mapping[str, str]) -> None:
    if _SKILL_NAME_RE.fullmatch(name) is None:
        raise HTTPException(status_code=422, detail="Invalid skill name.")
    if "SKILL.md" not in files:
        raise HTTPException(status_code=422, detail="A new skill needs SKILL.md.")
    if len(files) > _MAX_NEW_SKILL_FILES:
        raise HTTPException(status_code=422, detail="A skill can have at most 100 files.")
    if sum(len(path) + len(content) for path, content in files.items()) > _MAX_NEW_SKILL_CHARS:
        raise HTTPException(status_code=413, detail="The skill files exceed 1 MB.")
