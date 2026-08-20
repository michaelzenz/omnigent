"""REST routes for manual skill sync (``/v1/skills``)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from omnigent.server.auth import AuthProvider
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._host_filesystem import (
    HostFsError,
    HostFsUnavailableError,
    read_workspace_from_host,
)
from omnigent.stores.host_store import HostStore, host_is_live

_SKILL_HARNESSES = ("claude", "codex", "cursor")
_HARNESS_READINESS_KEYS = {
    "claude": ("claude", "claude-native", "native-claude"),
    "codex": ("codex", "codex-native", "native-codex"),
    "cursor": ("cursor", "cursor-native", "native-cursor"),
}
_NATIVE_SKILL_PATHS = {
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "cursor": ".cursor/skills",
}


def _harness_is_installed(
    readiness: Mapping[str, object] | None,
    harness: str,
) -> bool:
    if readiness is None:
        return False
    values = (readiness.get(key) for key in _HARNESS_READINESS_KEYS[harness])
    return any(value not in (None, False, "binary-missing") for value in values)


class SkillSyncRequest(BaseModel):
    """Choose one host's version and send it to the others."""

    source_host_id: str
    source_harness: str
    target_host_ids: list[str] | None = Field(
        default=None,
        description="Target host ids. Omit to sync every other registered host.",
    )

class SkillWriteRequest(BaseModel):
    host_id: str
    harness: str
    content: str


class SkillVariantWriteRequest(BaseModel):
    content: str


class SkillVariantFilesWriteRequest(BaseModel):
    files: dict[str, str]


class SkillHarnessSettingRequest(BaseModel):
    enabled: bool


def _owner_id(request: Request, auth_provider: AuthProvider | None) -> str:
    user_id = require_user(request, auth_provider)
    return user_id or "local"


def create_skills_router(
    host_registry: HostRegistry,
    host_store: HostStore,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build routes over inventories reported and mutated by each host."""
    router = APIRouter()

    def owner_hosts(owner: str):
        return host_store.list_hosts(owner)

    def inventory_entry(
        host_id: str,
        skill_name: str,
        harness: str,
    ) -> dict[str, str] | None:
        inventory = host_registry.skill_inventory(host_id) or []
        return next(
            (
                skill
                for skill in inventory
                if skill["name"] == skill_name and skill["harness"] == harness
            ),
            None,
        )

    async def host_skill_request(
        host_id: str,
        op: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        connection = host_registry.get(host_id)
        if connection is None:
            raise HTTPException(status_code=409, detail=f"Host {host_id!r} is offline.")
        try:
            payload = await read_workspace_from_host(
                host_registry=host_registry,
                host_conn=connection,
                op=op,
                workspace="",
                session_id="",
                params=params,
            )
        except HostFsError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        except HostFsUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        reported = payload.get("inventory")
        if isinstance(reported, list):
            host_registry.record_skill_inventory(host_id, reported)
        return payload

    @router.get("/skills/roots")
    async def list_skill_roots(request: Request) -> dict[str, Any]:
        """Return each host's database-backed skill configuration."""
        owner = _owner_id(request, auth_provider)
        data = []
        for host in owner_hosts(owner):
            data.append(
                {
                    "host_id": host.host_id,
                    "host_name": host.name,
                    "online": host_is_live(host),
                    "roots": host.skill_search_roots or [],
                    "sync_harnesses": host.skill_sync_harnesses,
                    "installed_harnesses": {
                        harness: _harness_is_installed(
                            host.configured_harnesses,
                            harness,
                        )
                        for harness in _SKILL_HARNESSES
                    },
                    "error": (
                        None
                        if host.skill_search_roots is not None
                        and host.skill_sync_harnesses is not None
                        else "Host has not reported skill configuration."
                    ),
                }
            )
        return {"object": "list", "data": data}

    @router.put("/skills/roots/{harness}")
    async def update_global_skill_harness_setting(
        request: Request,
        harness: str,
        body: SkillHarnessSettingRequest,
    ) -> dict[str, Any]:
        """Apply one harness's sync participation setting to every host."""
        if harness not in _SKILL_HARNESSES:
            raise HTTPException(status_code=404, detail="Harness not found.")
        owner = _owner_id(request, auth_provider)
        results = []
        for host in owner_hosts(owner):
            if host.skill_sync_harnesses is None:
                results.append(
                    {
                        "host_id": host.host_id,
                        "status": "settings_missing",
                    }
                )
                continue
            settings = dict(host.skill_sync_harnesses)
            settings[harness] = body.enabled
            await asyncio.to_thread(
                host_store.update_skill_sync_harnesses,
                host.host_id,
                settings,
            )
            status = "updated"
            if host_is_live(host):
                try:
                    payload = await host_skill_request(
                        host.host_id,
                        "skill.settings",
                        {"harness": harness, "enabled": body.enabled},
                    )
                    if payload.get("sync_harnesses") != settings:
                        status = "host_settings_failed"
                except HTTPException:
                    status = "host_settings_failed"
            results.append({"host_id": host.host_id, "status": status})
        return {
            "harness": harness,
            "enabled": body.enabled,
            "hosts": results,
        }

    @router.put("/skills/roots/{host_id}/{harness}")
    async def update_skill_harness_setting(
        request: Request,
        host_id: str,
        harness: str,
        body: SkillHarnessSettingRequest,
    ) -> dict[str, Any]:
        owner = _owner_id(request, auth_provider)
        host = next(
            (host for host in owner_hosts(owner) if host.host_id == host_id),
            None,
        )
        if host is None:
            raise HTTPException(status_code=404, detail="Host not found.")
        if harness not in _SKILL_HARNESSES:
            raise HTTPException(status_code=404, detail="Harness not found.")
        if host.skill_sync_harnesses is None:
            raise HTTPException(
                status_code=409,
                detail="Host has not reported skill configuration.",
            )
        settings = dict(host.skill_sync_harnesses)
        settings[harness] = body.enabled
        payload = await host_skill_request(
            host_id,
            "skill.settings",
            {"harness": harness, "enabled": body.enabled},
        )
        reported = payload.get("sync_harnesses")
        if reported != settings:
            raise HTTPException(status_code=502, detail="Host returned invalid settings.")
        await asyncio.to_thread(
            host_store.update_skill_sync_harnesses,
            host_id,
            settings,
        )
        return {"host_id": host_id, "sync_harnesses": settings}

    @router.get("/skills")
    async def list_skills(request: Request) -> dict[str, Any]:
        owner = _owner_id(request, auth_provider)
        hosts = owner_hosts(owner)
        inventories = {
            host.host_id: host_registry.skill_inventory(host.host_id) for host in hosts
        }
        names = sorted(
            {
                skill["name"]
                for inventory in inventories.values()
                for skill in inventory or []
            }
        )
        data = []
        for name in names:
            occurrences: list[dict[str, Any]] = []
            exemplar_description = ""
            for host in hosts:
                online = host_is_live(host)
                for entry in inventories[host.host_id] or []:
                    if entry["name"] != name:
                        continue
                    if not exemplar_description:
                        exemplar_description = entry["description"]
                    occurrences.append(
                        {
                            **entry,
                            "host_id": host.host_id,
                            "host_name": host.name,
                            "online": online,
                        }
                    )
            variants_by_hash: dict[str, list[dict[str, Any]]] = {}
            for occurrence in occurrences:
                variants_by_hash.setdefault(occurrence["content_sha256"], []).append(
                    occurrence
                )
            variants = [
                {
                    "content_sha256": content_hash,
                    "active_count": sum(item["online"] for item in items),
                    "occurrences": sorted(
                        items,
                        key=lambda item: (
                            not item["online"],
                            item["host_name"],
                            item["harness"],
                            item["rel_home_path"],
                        ),
                    ),
                }
                for content_hash, items in variants_by_hash.items()
            ]
            variants.sort(
                key=lambda variant: (
                    -variant["active_count"],
                    variant["content_sha256"],
                )
            )
            host_rows: list[dict[str, Any]] = []
            has_unavailable = False
            has_failure = not hosts
            participating_hashes: set[str] = set()
            for host in hosts:
                online = host_is_live(host)
                reported = inventories[host.host_id] is not None
                sync_harnesses = host.skill_sync_harnesses
                harness_rows = []
                for harness in _SKILL_HARNESSES:
                    enabled = (
                        sync_harnesses[harness]
                        if sync_harnesses is not None
                        else None
                    )
                    installed = _harness_is_installed(
                        host.configured_harnesses,
                        harness,
                    )
                    entry = next(
                        (
                            item
                            for item in occurrences
                            if item["host_id"] == host.host_id
                            and item["harness"] == harness
                        ),
                        None,
                    )
                    if enabled is None:
                        state = "not_reported"
                        has_failure = True
                    elif not enabled:
                        state = "ignored"
                    elif not installed:
                        state = "unavailable"
                        has_unavailable = True
                    elif not online:
                        state = "offline"
                        has_failure = True
                    elif not reported:
                        state = "not_reported"
                        has_failure = True
                    elif entry is None:
                        state = "missing"
                        has_failure = True
                    else:
                        state = "present"
                        participating_hashes.add(entry["content_sha256"])
                    harness_rows.append(
                        {
                            "harness": harness,
                            "installed": installed,
                            "enabled": enabled,
                            "state": state,
                            "occurrence": entry,
                        }
                    )
                omnigent_entry = next(
                    (
                        item
                        for item in occurrences
                        if item["host_id"] == host.host_id
                        and item["harness"] == "omnigent"
                    ),
                    None,
                )
                if omnigent_entry is not None:
                    omnigent_state = "present" if online and reported else "offline"
                    if omnigent_state == "present":
                        participating_hashes.add(omnigent_entry["content_sha256"])
                    else:
                        has_failure = True
                    harness_rows.append(
                        {
                            "harness": "omnigent",
                            "installed": True,
                            "enabled": True,
                            "state": omnigent_state,
                            "occurrence": omnigent_entry,
                        }
                    )
                host_rows.append(
                    {
                        "host_id": host.host_id,
                        "host_name": host.name,
                        "online": online,
                        "reported": reported,
                        "harnesses": harness_rows,
                    }
                )
            for host_row in host_rows:
                visible_harnesses = []
                for harness_row in host_row["harnesses"]:
                    if harness_row["state"] != "ignored":
                        visible_harnesses.append(harness_row)
                        continue
                    ignored_occurrence = harness_row["occurrence"]
                    if (
                        ignored_occurrence is not None
                        and (
                            not participating_hashes
                            or ignored_occurrence["content_sha256"]
                            not in participating_hashes
                        )
                    ):
                        harness_row["state"] = "ignored_variant"
                        visible_harnesses.append(harness_row)
                host_row["harnesses"] = visible_harnesses
            if len(participating_hashes) > 1:
                has_failure = True
            sync_status = (
                "not_synced"
                if has_failure
                else "partial"
                if has_unavailable
                else "synced"
            )
            data.append(
                {
                    "name": name,
                    "description": exemplar_description,
                    "synced": sync_status == "synced",
                    "sync_status": sync_status,
                    "variants": variants,
                    "hosts": host_rows,
                }
            )
        return {"object": "list", "data": data}

    @router.get("/skills/{skill_name}/content")
    async def read_skill(
        request: Request,
        skill_name: str,
        host_id: str = Query(...),
        harness: str = Query(...),
    ) -> dict[str, Any]:
        owner = _owner_id(request, auth_provider)
        if not any(host.host_id == host_id for host in owner_hosts(owner)):
            raise HTTPException(status_code=404, detail="Host not found.")
        entry = inventory_entry(host_id, skill_name, harness)
        if entry is None:
            raise HTTPException(status_code=404, detail="Skill not found on host.")
        payload = await host_skill_request(
            host_id,
            "skill.read",
            {"name": skill_name, "rel_home_path": entry["rel_home_path"]},
        )
        return {"host_id": host_id, "name": skill_name, "content": payload["content"]}

    @router.get("/skills/{skill_name}/tree")
    async def read_skill_tree(
        request: Request,
        skill_name: str,
        host_id: str = Query(...),
        harness: str = Query(...),
    ) -> dict[str, Any]:
        """Read every file in one reported skill directory."""
        owner = _owner_id(request, auth_provider)
        if not any(host.host_id == host_id for host in owner_hosts(owner)):
            raise HTTPException(status_code=404, detail="Host not found.")
        entry = inventory_entry(host_id, skill_name, harness)
        if entry is None:
            raise HTTPException(status_code=404, detail="Skill not found on host.")
        payload = await host_skill_request(
            host_id,
            "skill.export",
            {"name": skill_name, "rel_home_path": entry["rel_home_path"]},
        )
        encoded_files = payload.get("files")
        if not isinstance(encoded_files, dict):
            raise HTTPException(status_code=502, detail="Host returned an invalid skill tree.")
        files = []
        for path, encoded in encoded_files.items():
            if not isinstance(path, str) or not isinstance(encoded, str):
                raise HTTPException(status_code=502, detail="Host returned an invalid skill tree.")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise HTTPException(
                    status_code=502,
                    detail="Host returned an invalid skill file.",
                ) from exc
            try:
                content = raw.decode("utf-8")
                binary = False
            except UnicodeDecodeError:
                content = f"Binary file (sha256: {hashlib.sha256(raw).hexdigest()})"
                binary = True
            files.append({"path": path, "content": content, "binary": binary})
        files.sort(
            key=lambda file: (
                file["path"].lower() != "skill.md",
                file["path"].lower(),
            )
        )
        return {
            "host_id": host_id,
            "name": skill_name,
            "rel_home_path": entry["rel_home_path"],
            "files": files,
        }

    @router.put("/skills/{skill_name}/variants/{content_sha256}/content")
    async def write_skill_variant(
        request: Request,
        skill_name: str,
        content_sha256: str,
        body: SkillVariantWriteRequest,
    ) -> dict[str, Any]:
        """Write the edited content to every occurrence in one variant."""
        owner = _owner_id(request, auth_provider)
        occurrences = [
            (host.host_id, entry)
            for host in owner_hosts(owner)
            for entry in host_registry.skill_inventory(host.host_id) or []
            if entry["name"] == skill_name
            and entry["content_sha256"] == content_sha256
        ]
        if not occurrences:
            raise HTTPException(status_code=404, detail="Skill variant not found.")
        results = []
        for host_id, entry in occurrences:
            try:
                await host_skill_request(
                    host_id,
                    "skill.write",
                    {
                        "name": skill_name,
                        "rel_home_path": entry["rel_home_path"],
                        "content": body.content,
                    },
                )
                results.append(
                    {
                        "host_id": host_id,
                        "harness": entry["harness"],
                        "status": "saved",
                    }
                )
            except HTTPException as exc:
                results.append(
                    {
                        "host_id": host_id,
                        "harness": entry["harness"],
                        "status": "failed",
                        "error": exc.detail,
                    }
                )
        return {"object": "skill_variant_write_result", "results": results}

    @router.put("/skills/{skill_name}/variants/{content_sha256}/files")
    async def write_skill_variant_files(
        request: Request,
        skill_name: str,
        content_sha256: str,
        body: SkillVariantFilesWriteRequest,
    ) -> dict[str, Any]:
        """Write edited text files to every occurrence in one variant."""
        owner = _owner_id(request, auth_provider)
        occurrences = [
            (host.host_id, entry)
            for host in owner_hosts(owner)
            for entry in host_registry.skill_inventory(host.host_id) or []
            if entry["name"] == skill_name
            and entry["content_sha256"] == content_sha256
        ]
        if not occurrences:
            raise HTTPException(status_code=404, detail="Skill variant not found.")
        results = []
        for host_id, entry in occurrences:
            try:
                await host_skill_request(
                    host_id,
                    "skill.files.write",
                    {
                        "name": skill_name,
                        "rel_home_path": entry["rel_home_path"],
                        "files": body.files,
                    },
                )
                results.append(
                    {
                        "host_id": host_id,
                        "harness": entry["harness"],
                        "status": "saved",
                    }
                )
            except HTTPException as exc:
                results.append(
                    {
                        "host_id": host_id,
                        "harness": entry["harness"],
                        "status": "failed",
                        "error": str(exc.detail),
                    }
                )
        return {"object": "skill_variant_files_write_result", "results": results}

    @router.put("/skills/{skill_name}/content")
    async def write_skill(
        request: Request,
        skill_name: str,
        body: SkillWriteRequest,
    ) -> dict[str, Any]:
        owner = _owner_id(request, auth_provider)
        if not any(host.host_id == body.host_id for host in owner_hosts(owner)):
            raise HTTPException(status_code=404, detail="Host not found.")
        entry = inventory_entry(body.host_id, skill_name, body.harness)
        if entry is None:
            raise HTTPException(status_code=404, detail="Skill not found on host.")
        await host_skill_request(
            body.host_id,
            "skill.write",
            {
                "name": skill_name,
                "rel_home_path": entry["rel_home_path"],
                "content": body.content,
            },
        )
        return {"object": "skill_write_result", "host_id": body.host_id, "name": skill_name}

    @router.post("/skills/{skill_name}/sync")
    async def sync_skill(
        request: Request,
        skill_name: str,
        body: SkillSyncRequest,
    ) -> dict[str, Any]:
        owner = _owner_id(request, auth_provider)
        hosts = owner_hosts(owner)
        by_id = {host.host_id: host for host in hosts}
        source_entry = inventory_entry(
            body.source_host_id,
            skill_name,
            body.source_harness,
        )
        if body.source_host_id not in by_id or source_entry is None:
            raise HTTPException(status_code=404, detail="Source skill version not found.")
        exported = await host_skill_request(
            body.source_host_id,
            "skill.export",
            {"name": skill_name, "rel_home_path": source_entry["rel_home_path"]},
        )
        targets = body.target_host_ids or [host.host_id for host in hosts]
        results = []
        for host_id in targets:
            if host_id not in by_id:
                results.append({"host_id": host_id, "status": "not_found"})
                continue
            host = by_id[host_id]
            inventory = host_registry.skill_inventory(host_id) or []
            sync_harnesses = host.skill_sync_harnesses
            if sync_harnesses is None:
                results.append(
                    {
                        "host_id": host_id,
                        "status": "settings_missing",
                    }
                )
                continue
            for harness in _SKILL_HARNESSES:
                if not sync_harnesses[harness]:
                    results.append(
                        {
                            "host_id": host_id,
                            "harness": harness,
                            "status": "ignored",
                        }
                    )
                    continue
                if not _harness_is_installed(host.configured_harnesses, harness):
                    results.append(
                        {
                            "host_id": host_id,
                            "harness": harness,
                            "status": "unavailable",
                        }
                    )
                    continue
                target_entry = next(
                    (
                        entry
                        for entry in inventory
                        if entry["name"] == skill_name and entry["harness"] == harness
                    ),
                    None,
                )
                rel_home_path = (
                    target_entry["rel_home_path"]
                    if target_entry is not None
                    else f"{_NATIVE_SKILL_PATHS[harness]}/{skill_name}"
                )
                try:
                    await host_skill_request(
                        host_id,
                        "skill.import",
                        {
                            "rel_home_path": rel_home_path,
                            "files": exported["files"],
                        },
                    )
                    results.append(
                        {
                            "host_id": host_id,
                            "harness": harness,
                            "status": "synced",
                        }
                    )
                except HTTPException as exc:
                    results.append(
                        {
                            "host_id": host_id,
                            "harness": harness,
                            "status": "failed",
                            "error": exc.detail,
                        }
                    )
            omnigent_entry = next(
                (
                    entry
                    for entry in inventory
                    if entry["name"] == skill_name
                    and entry["harness"] == "omnigent"
                ),
                None,
            )
            if omnigent_entry is not None:
                try:
                    await host_skill_request(
                        host_id,
                        "skill.import",
                        {
                            "rel_home_path": omnigent_entry["rel_home_path"],
                            "files": exported["files"],
                        },
                    )
                    results.append(
                        {
                            "host_id": host_id,
                            "harness": "omnigent",
                            "status": "synced",
                        }
                    )
                except HTTPException as exc:
                    results.append(
                        {
                            "host_id": host_id,
                            "harness": "omnigent",
                            "status": "failed",
                            "error": exc.detail,
                        }
                    )
        return {"object": "skill_sync_result", "name": skill_name, "hosts": results}

    @router.delete("/skills/{skill_name}")
    async def delete_skill(request: Request, skill_name: str) -> dict[str, Any]:
        owner = _owner_id(request, auth_provider)
        results = []
        for host in owner_hosts(owner):
            entries = [
                entry
                for entry in host_registry.skill_inventory(host.host_id) or []
                if entry["name"] == skill_name
            ]
            if not entries:
                results.append({"host_id": host.host_id, "status": "missing"})
                continue
            for entry in entries:
                try:
                    await host_skill_request(
                        host.host_id,
                        "skill.delete",
                        {"name": skill_name, "rel_home_path": entry["rel_home_path"]},
                    )
                    results.append(
                        {
                            "host_id": host.host_id,
                            "harness": entry["harness"],
                            "status": "deleted",
                        }
                    )
                except HTTPException as exc:
                    results.append(
                        {
                            "host_id": host.host_id,
                            "harness": entry["harness"],
                            "status": "failed",
                            "error": exc.detail,
                        }
                    )
        return {"object": "skill_delete_result", "name": skill_name, "hosts": results}

    return router
