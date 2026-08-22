"""Tests for the in-memory skill sync manifest."""

from __future__ import annotations

from pathlib import Path

from omnigent.server.skill_sync_registry import (
    SkillSyncRegistry,
    build_skill_sync_manifest,
    skill_dir_sha256,
)


def _write_skill(root: Path, rel: str, name: str, description: str = "desc") -> Path:
    skill_dir = root / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nbody\n",
        encoding="utf-8",
    )
    return skill_dir


def test_build_manifest_includes_claude_skills(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    skill_dir = _write_skill(home, ".claude/skills/alpha", "alpha", "Alpha skill")
    manifest = build_skill_sync_manifest(home)
    assert set(manifest) == {"alpha"}
    entry = manifest["alpha"]
    assert entry.rel_home_path == ".claude/skills/alpha"
    assert entry.abs_path == skill_dir.resolve()
    assert entry.content_sha256 == skill_dir_sha256(skill_dir)


def test_omnigent_override_wins_on_name_collision(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_skill(home, ".claude/skills/shared", "shared", "from claude")
    override_dir = _write_skill(home, ".omnigent/skills/shared", "shared", "override")
    manifest = build_skill_sync_manifest(home)
    assert manifest["shared"].abs_path == override_dir.resolve()
    assert manifest["shared"].rel_home_path == ".omnigent/skills/shared"


def test_omnigent_override_ignored_when_directory_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_skill(home, ".claude/skills/only", "only")
    assert not (home / ".omnigent" / "skills").exists()
    manifest = build_skill_sync_manifest(home)
    assert manifest["only"].rel_home_path == ".claude/skills/only"


def test_registry_refresh_replaces_entries(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    registry = SkillSyncRegistry(home=home)
    assert registry.entries() == []
    _write_skill(home, ".claude/skills/new", "new")
    registry.refresh()
    assert [entry.name for entry in registry.entries()] == ["new"]
