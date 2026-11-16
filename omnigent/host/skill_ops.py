"""Skill sync operations that the server asks a host to run on its home skill folders.

The server reads with ``host.fs_request`` (``SKILL_READ_OPS``) and changes
files with ``host.fs_write_request`` (``SKILL_WRITE_OPS``). Every path stays
inside a harness skill folder under the home directory. Each host keeps its
own sync settings, next to its Omnigent config.
"""

from __future__ import annotations

import base64
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from omnigent.config import global_config_path
from omnigent.host.skill_sync import (
    SkillSyncEntry,
    build_skill_occurrences,
    build_skill_search_roots,
    skill_dir_sha256,
)
from omnigent.spec.parser import _parse_skill

SKILL_READ_OPS = frozenset({"skill.inventory", "skill.roots", "skill.read", "skill.export"})
SKILL_WRITE_OPS = frozenset(
    {"skill.settings", "skill.import", "skill.write", "skill.files.write", "skill.delete"}
)
_SKILL_HARNESSES = ("claude", "codex", "cursor")
_SYNC_SETTINGS_FILE = "skill-sync.json"


def skill_inventory_wire(home: Path | None = None) -> list[dict[str, object]]:
    """Return each harness's home skill copies, without file contents."""
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
    """Return every home folder that a harness searches for skills."""
    return [
        {"harness": harness, "rel_home_path": rel_home_path}
        for harness, rel_home_path in build_skill_search_roots(home)
    ]


def skill_sync_settings_wire() -> dict[str, bool]:
    """Return whether each harness takes part in sync on this host; all do by default."""
    settings = dict.fromkeys(_SKILL_HARNESSES, True)
    try:
        raw = json.loads(_sync_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if isinstance(raw, dict):
        for harness in _SKILL_HARNESSES:
            value = raw.get(harness)
            if isinstance(value, bool):
                settings[harness] = value
    return settings


def handle_skill_fs_op(op: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Run one skill sync operation on this host. Blocking.

    :param op: One of ``SKILL_READ_OPS`` or ``SKILL_WRITE_OPS``, e.g. ``"skill.export"``.
    :param params: The operation's arguments, e.g. ``{"name": "pdf",
        "rel_home_path": ".claude/skills/pdf"}``. A change to a reported skill
        can carry ``expected_content_sha256``, so a stale copy is refused.
    :returns: The operation's payload; a change returns the new inventory.
    :raises ValueError: For bad arguments, a path outside the skill folders, or
        a stale copy.
    :raises FileNotFoundError: When the skill is not on this host.
    :raises FileExistsError: When a create-only import finds the skill.
    """
    home = Path.home().resolve()
    if op == "skill.inventory":
        return _inventory_payload(home)
    if op == "skill.roots":
        return {
            "roots": skill_search_roots_wire(home),
            "sync_harnesses": skill_sync_settings_wire(),
        }
    if op == "skill.settings":
        harness = params.get("harness")
        enabled = params.get("enabled")
        if not isinstance(harness, str) or not isinstance(enabled, bool):
            raise ValueError("skill settings require harness and enabled")
        return {"sync_harnesses": _write_skill_sync_setting(harness, enabled)}
    if op == "skill.import":
        return _import(home, params)

    entry = _entry_for(params, home)
    expected_hash = params.get("expected_content_sha256")
    if expected_hash is not None:
        if not isinstance(expected_hash, str):
            raise ValueError("expected skill hash must be a string")
        if entry.content_sha256 != expected_hash:
            raise ValueError("skill changed since this variant was selected")
    skill_dir = entry.abs_path
    if op == "skill.read":
        return {"content": (skill_dir / "SKILL.md").read_text(encoding="utf-8")}
    if op == "skill.write":
        content = params.get("content")
        if not isinstance(content, str):
            raise ValueError("skill write requires string content")
        _write_skill_files(
            skill_dir, {"SKILL.md": content}, expected_name=_frontmatter_name(skill_dir)
        )
        return _inventory_payload(home)
    if op == "skill.files.write":
        files = params.get("files")
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and isinstance(content, str) for path, content in files.items()
        ):
            raise ValueError("skill file write requires a string file map")
        _write_skill_files(skill_dir, files, expected_name=_frontmatter_name(skill_dir))
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


def _sync_settings_path() -> Path:
    return global_config_path().parent / _SYNC_SETTINGS_FILE


def _write_skill_sync_setting(harness: str, enabled: bool) -> dict[str, bool]:
    if harness not in _SKILL_HARNESSES:
        raise ValueError("unknown skill harness")
    settings = skill_sync_settings_wire()
    settings[harness] = enabled
    path = _sync_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)
    return settings


def _inventory_payload(home: Path) -> dict[str, Any]:
    return {"inventory": skill_inventory_wire(home)}


def _import(home: Path, params: dict[str, Any]) -> dict[str, Any]:
    rel = params.get("rel_home_path")
    files = params.get("files")
    expected_name = params.get("name")
    create_only = params.get("create_only", False)
    if not isinstance(rel, str) or not isinstance(files, dict):
        raise ValueError("skill import requires rel_home_path and files")
    if expected_name is not None and not isinstance(expected_name, str):
        raise ValueError("skill import name must be a string")
    if not isinstance(create_only, bool):
        raise ValueError("skill import create_only must be a boolean")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in files.items()):
        raise ValueError("skill import files must be base64 strings")
    skill_dir = _safe_skill_dir(rel, home, must_exist=False)
    if create_only and skill_dir.exists():
        raise FileExistsError("skill already exists on host")
    _write_tree(skill_dir, files, expected_name=expected_name)
    return _inventory_payload(home)


def _safe_skill_dir(rel_home_path: str, home: Path, *, must_exist: bool) -> Path:
    rel = PurePosixPath(rel_home_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("invalid skill path")
    allowed = rel.parts[:2] in {(".agents", "skills"), (".omnigent", "skills")} or (
        len(rel.parts) >= 3
        and rel.parts[0] in {".claude", ".cursor", ".codex"}
        and "skills" in rel.parts[1:-1]
    )
    if not allowed:
        raise ValueError("path is outside host skill roots")
    path = (home / Path(*rel.parts)).resolve()
    path.relative_to(home)
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
    # A plugin skill is "<plugin>:<skill>" in the inventory; its SKILL.md names only "<skill>".
    if isinstance(name, str) and spec.name not in {name, name.rsplit(":", 1)[-1]}:
        raise FileNotFoundError("reported skill no longer matches path")
    return SkillSyncEntry(
        name=spec.name,
        description=spec.description,
        rel_home_path=rel,
        abs_path=skill_dir,
        content_sha256=skill_dir_sha256(skill_dir),
    )


def _frontmatter_name(skill_md_or_dir: Path) -> str:
    """Return the ``name`` in a ``SKILL.md``'s frontmatter; a folder means its ``SKILL.md``."""
    skill_md = skill_md_or_dir / "SKILL.md" if skill_md_or_dir.is_dir() else skill_md_or_dir
    spec = _parse_skill(skill_md)
    # _parse_skill names a skill after its folder and keeps a different frontmatter name apart.
    return spec.display_name or spec.name


def _editable_skill_file(skill_dir: Path, raw_path: str) -> Path:
    rel = PurePosixPath(raw_path)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise ValueError("invalid skill file path")
    candidate = skill_dir / Path(*rel.parts)
    if candidate.is_symlink():
        raise ValueError("skill symlinks cannot be edited")
    path = candidate.resolve()
    path.relative_to(skill_dir.resolve())
    if not path.is_file():
        raise FileNotFoundError("skill file does not exist")
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("binary skill files cannot be edited") from exc
    return path


def _write_skill_files(skill_dir: Path, files: dict[str, str], *, expected_name: str) -> None:
    """Replace existing text files of a skill; all of them or none."""
    destinations = {
        _editable_skill_file(skill_dir, raw_path): content for raw_path, content in files.items()
    }
    temporary: list[tuple[Path, Path]] = []
    try:
        for destination, content in destinations.items():
            temp = destination.with_name(f".{destination.name}.omnigent.tmp")
            temp.write_text(content, encoding="utf-8")
            temporary.append((temp, destination))
        for temp, destination in temporary:
            if destination.name == "SKILL.md" and _frontmatter_name(temp) != expected_name:
                raise ValueError("SKILL.md frontmatter name cannot be changed")
        for temp, destination in temporary:
            temp.replace(destination)
    finally:
        for temp, _destination in temporary:
            temp.unlink(missing_ok=True)


def _write_tree(skill_dir: Path, files: dict[str, str], *, expected_name: str | None) -> None:
    """Replace a whole skill folder with base64-encoded files; the old folder stays on failure."""
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
        if expected_name is not None and _frontmatter_name(temp) != expected_name:
            raise ValueError("SKILL.md frontmatter name does not match the requested skill")
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
