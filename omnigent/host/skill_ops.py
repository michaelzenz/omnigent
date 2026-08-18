"""Host-local skill discovery and file operations."""

from __future__ import annotations

import base64
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from omnigent.server.skill_sync_registry import (
    SkillSyncEntry,
    build_skill_occurrences,
    build_skill_search_roots,
    skill_dir_sha256,
)
from omnigent.spec.parser import _parse_skill

_SKILL_HARNESSES = ("claude", "codex", "cursor")
_SYNC_SETTINGS_PATH = Path(".omnigent/skill-sync.json")


def skill_inventory_wire(home: Path | None = None) -> list[dict[str, object]]:
    """Discover this host's skills and return transport-safe metadata."""
    return [
        {
            "name": entry.name,
            "description": entry.description,
            "harness": harness,
            "rel_home_path": entry.rel_home_path,
            "content_sha256": entry.content_sha256,
        }
        for harness, entry in build_skill_occurrences(home)
    ]


def skill_search_roots_wire(home: Path | None = None) -> list[dict[str, object]]:
    """Return every skill search directory grouped by harness."""
    return [
        {"harness": harness, "rel_home_path": rel_home_path}
        for harness, rel_home_path in build_skill_search_roots(home)
    ]


def skill_sync_settings_wire(home: Path | None = None) -> dict[str, bool]:
    """Read this host's persisted per-harness sync participation."""
    resolved_home = (home or Path.home()).resolve()
    settings = dict.fromkeys(_SKILL_HARNESSES, True)
    path = resolved_home / _SYNC_SETTINGS_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return settings
    if isinstance(raw, dict):
        for harness in _SKILL_HARNESSES:
            value = raw.get(harness)
            if isinstance(value, bool):
                settings[harness] = value
    return settings


def _write_skill_sync_setting(home: Path, harness: str, enabled: bool) -> dict[str, bool]:
    if harness not in _SKILL_HARNESSES:
        raise ValueError("unknown skill harness")
    settings = skill_sync_settings_wire(home)
    settings[harness] = enabled
    path = home / _SYNC_SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)
    return settings


def _safe_skill_dir(rel_home_path: str, home: Path, *, must_exist: bool) -> Path:
    rel = PurePosixPath(rel_home_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("invalid skill path")
    allowed = rel.parts[:2] in {
        (".agents", "skills"),
        (".omnigent", "skills"),
    } or (
        len(rel.parts) >= 3
        and rel.parts[0] in {".claude", ".cursor", ".codex"}
        and "skills" in rel.parts[1:-1]
    )
    if not allowed:
        raise ValueError("path is outside host skill roots")
    path = (home / Path(*rel.parts)).resolve()
    path.relative_to(home.resolve())
    if must_exist and not (path / "SKILL.md").is_file():
        raise FileNotFoundError("skill does not exist on host")
    return path


def _entry_for(params: dict[str, Any], home: Path) -> SkillSyncEntry:
    name = params.get("name")
    rel = params.get("rel_home_path")
    if not isinstance(rel, str):
        raise FileNotFoundError("skill path was not reported by host")
    skill_dir = _safe_skill_dir(rel, home, must_exist=True)
    spec = _parse_skill(skill_dir / "SKILL.md")
    if isinstance(name, str) and spec.name != name:
        raise FileNotFoundError("reported skill no longer matches path")
    return SkillSyncEntry(
        name=spec.name,
        description=spec.description,
        rel_home_path=rel,
        abs_path=skill_dir,
        content_sha256=skill_dir_sha256(skill_dir),
    )


def _inventory_payload(home: Path) -> dict[str, Any]:
    return {"inventory": skill_inventory_wire(home)}


def _write_tree(skill_dir: Path, files: dict[str, str]) -> None:
    skill_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{skill_dir.name}-", dir=skill_dir.parent))
    backup = skill_dir.with_name(f".{skill_dir.name}.backup")
    try:
        for raw_rel, encoded in files.items():
            rel = PurePosixPath(raw_rel)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("invalid file path in skill archive")
            destination = temp / Path(*rel.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(base64.b64decode(encoded, validate=True))
        if not (temp / "SKILL.md").is_file():
            raise ValueError("skill archive is missing SKILL.md")
        if backup.exists():
            shutil.rmtree(backup)
        if skill_dir.exists():
            skill_dir.rename(backup)
        temp.rename(skill_dir)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        if backup.exists() and not skill_dir.exists():
            backup.rename(skill_dir)
        raise


def handle_skill_fs_op(op: str, params: dict[str, Any]) -> dict[str, Any]:
    """Execute a server-requested operation entirely on this host."""
    home = Path.home().resolve()
    if op == "skill.roots":
        return {
            "roots": skill_search_roots_wire(home),
            "sync_harnesses": skill_sync_settings_wire(home),
        }
    if op == "skill.settings":
        harness = params.get("harness")
        enabled = params.get("enabled")
        if not isinstance(harness, str) or not isinstance(enabled, bool):
            raise ValueError("skill settings require harness and enabled")
        return {"sync_harnesses": _write_skill_sync_setting(home, harness, enabled)}
    if op == "skill.import":
        rel = params.get("rel_home_path")
        files = params.get("files")
        if not isinstance(rel, str) or not isinstance(files, dict):
            raise ValueError("skill import requires rel_home_path and files")
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in files.items()
        ):
            raise ValueError("skill import files must be base64 strings")
        _write_tree(_safe_skill_dir(rel, home, must_exist=False), files)
        return _inventory_payload(home)

    entry = _entry_for(params, home)
    skill_dir = _safe_skill_dir(entry.rel_home_path, home, must_exist=True)
    if op == "skill.read":
        return {"content": (skill_dir / "SKILL.md").read_text(encoding="utf-8")}
    if op == "skill.write":
        content = params.get("content")
        if not isinstance(content, str):
            raise ValueError("skill write requires string content")
        destination = skill_dir / "SKILL.md"
        temp = destination.with_suffix(".md.tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(destination)
        return _inventory_payload(home)
    if op == "skill.export":
        files = {
            path.relative_to(skill_dir).as_posix(): base64.b64encode(path.read_bytes()).decode()
            for path in sorted(skill_dir.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }
        return {"files": files, "rel_home_path": entry.rel_home_path}
    if op == "skill.delete":
        shutil.rmtree(skill_dir)
        return _inventory_payload(home)
    raise ValueError(f"unsupported skill operation: {op}")
