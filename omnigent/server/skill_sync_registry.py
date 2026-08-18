"""In-memory inventory of user-global skills for manual cross-host sync."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from omnigent.spec.parser import _discover_skills, _parse_skill
from omnigent.spec.skill_sources import SkillSourceContext, _claude_plugin_skills

_log = logging.getLogger(__name__)

# User-global roots scanned for the sync manifest (workspace skills are excluded).
_GLOBAL_SKILL_ROOTS: tuple[str, ...] = (
    ".claude/skills",
    ".agents/skills",
    ".cursor/skills",
    ".codex/skills",
)

_OVERRIDE_ROOT = ".omnigent/skills"


@dataclass(frozen=True)
class SkillSyncEntry:
    """One skill in the sync manifest."""

    name: str
    rel_home_path: str
    abs_path: Path
    content_sha256: str
    description: str


def skill_dir_sha256(skill_dir: Path) -> str:
    """Content hash of a skill directory tree (paths + file bytes)."""
    digest = hashlib.sha256()
    if not skill_dir.is_dir():
        return digest.hexdigest()
    for file_path in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        rel = file_path.relative_to(skill_dir).as_posix().encode()
        digest.update(rel)
        digest.update(file_path.read_bytes())
    return digest.hexdigest()


def _rel_home_path(home: Path, skill_dir: Path) -> str | None:
    try:
        rel = Path(os.path.abspath(skill_dir)).relative_to(home.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def _scan_skills_dir(
    skills_dir: Path,
    home: Path,
    *,
    skipped: list[str],
    replace_names: set[str] | None = None,
) -> dict[str, SkillSyncEntry]:
    """Parse skills under *skills_dir*; map surfaced name → entry."""
    out: dict[str, SkillSyncEntry] = {}
    if not skills_dir.is_dir():
        return out
    for spec in _discover_skills(skills_dir, skipped=skipped):
        if spec.skill_dir is None:
            continue
        rel = _rel_home_path(home, spec.skill_dir)
        if rel is None:
            continue
        out[spec.name] = SkillSyncEntry(
            name=spec.name,
            rel_home_path=rel,
            abs_path=spec.skill_dir.resolve(),
            content_sha256=skill_dir_sha256(spec.skill_dir),
            description=spec.description,
        )
    if replace_names:
        for name in list(out):
            if name not in replace_names:
                del out[name]
    return out


def _scan_codex_skills(home: Path, *, skipped: list[str]) -> dict[str, SkillSyncEntry]:
    from omnigent.inner.codex_executor import codex_skill_sources, select_codex_skill_dirs

    out: dict[str, SkillSyncEntry] = {}
    sources = codex_skill_sources(None, home)
    for name, skill_dir in select_codex_skill_dirs("all", sources).items():
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            spec = _parse_skill(skill_md)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{skill_md}: {exc}")
            continue
        rel = _rel_home_path(home, skill_dir)
        if rel is None:
            continue
        out[name] = SkillSyncEntry(
            name=name,
            rel_home_path=rel,
            abs_path=skill_dir.resolve(),
            content_sha256=skill_dir_sha256(skill_dir),
            description=spec.description,
        )
    return out


def _scan_cursor_skills(home: Path, *, skipped: list[str]) -> dict[str, SkillSyncEntry]:
    skills_dir = home / ".cursor" / "skills"
    out: dict[str, SkillSyncEntry] = {}
    if not skills_dir.is_dir():
        return out
    try:
        children = sorted(skills_dir.iterdir())
    except OSError as exc:
        skipped.append(f"{skills_dir}: {exc}")
        return out
    for child in children:
        if not child.is_dir() or not (child / "SKILL.md").is_file():
            continue
        try:
            spec = _parse_skill(child / "SKILL.md")
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{child / 'SKILL.md'}: {exc}")
            continue
        rel = _rel_home_path(home, child)
        if rel is None:
            continue
        out[child.name] = SkillSyncEntry(
            name=child.name,
            rel_home_path=rel,
            abs_path=child.resolve(),
            content_sha256=skill_dir_sha256(child),
            description=spec.description,
        )
    return out


def build_skill_sync_manifest(home: Path | None = None) -> dict[str, SkillSyncEntry]:
    """
    Scan user-global skill locations and return the deduplicated manifest.

    ``~/.omnigent/skills/`` is scanned last and overrides same-named skills
    from other roots when the directory exists.
    """
    resolved_home = (home or Path.home()).resolve()
    skipped: list[str] = []
    entries: dict[str, SkillSyncEntry] = {}

    for rel_root in _GLOBAL_SKILL_ROOTS:
        if rel_root == ".codex/skills":
            entries.update(_scan_codex_skills(resolved_home, skipped=skipped))
            continue
        if rel_root == ".cursor/skills":
            entries.update(_scan_cursor_skills(resolved_home, skipped=skipped))
            continue
        entries.update(
            _scan_skills_dir(resolved_home / rel_root, resolved_home, skipped=skipped)
        )

    ctx = SkillSourceContext(
        roots=(),
        home=resolved_home,
        skills_filter="all",
        bundle_dir=None,
    )
    for spec in _claude_plugin_skills(ctx):
        if spec.skill_dir is None:
            continue
        rel = _rel_home_path(resolved_home, spec.skill_dir)
        if rel is None:
            continue
        entries[spec.name] = SkillSyncEntry(
            name=spec.name,
            rel_home_path=rel,
            abs_path=spec.skill_dir.resolve(),
            content_sha256=skill_dir_sha256(spec.skill_dir),
            description=spec.description,
        )

    override_dir = resolved_home / _OVERRIDE_ROOT
    if override_dir.is_dir():
        override_entries = _scan_skills_dir(
            override_dir,
            resolved_home,
            skipped=skipped,
        )
        entries.update(override_entries)

    for detail in skipped:
        _log.warning("Skill manifest scan skipped: %s", detail)
    return entries


def build_skill_occurrences(
    home: Path | None = None,
) -> list[tuple[str, SkillSyncEntry]]:
    """Discover the effective skill copy for each harness on this host."""
    resolved_home = (home or Path.home()).resolve()
    skipped: list[str] = []
    occurrences: list[tuple[str, SkillSyncEntry]] = []

    claude = _scan_skills_dir(
        resolved_home / ".claude" / "skills",
        resolved_home,
        skipped=skipped,
    )
    ctx = SkillSourceContext(
        roots=(),
        home=resolved_home,
        skills_filter="all",
        bundle_dir=None,
    )
    for spec in _claude_plugin_skills(ctx):
        if spec.skill_dir is None:
            continue
        rel = _rel_home_path(resolved_home, spec.skill_dir)
        if rel is None:
            continue
        claude[spec.name] = SkillSyncEntry(
            name=spec.name,
            rel_home_path=rel,
            abs_path=spec.skill_dir.resolve(),
            content_sha256=skill_dir_sha256(spec.skill_dir),
            description=spec.description,
        )
    occurrences.extend(("claude", entry) for entry in claude.values())
    occurrences.extend(
        ("cursor", entry)
        for entry in _scan_cursor_skills(resolved_home, skipped=skipped).values()
    )
    occurrences.extend(
        ("codex", entry)
        for entry in _scan_codex_skills(resolved_home, skipped=skipped).values()
    )
    occurrences.extend(
        ("omnigent", entry)
        for entry in _scan_skills_dir(
            resolved_home / ".omnigent" / "skills",
            resolved_home,
            skipped=skipped,
        ).values()
    )
    for detail in skipped:
        _log.warning("Skill inventory scan skipped: %s", detail)
    return sorted(occurrences, key=lambda item: (item[1].name, item[0]))


def build_skill_search_roots(home: Path | None = None) -> list[tuple[str, str]]:
    """Return every user-global directory searched for each harness."""
    resolved_home = (home or Path.home()).resolve()
    roots: set[tuple[str, str]] = {
        ("claude", ".claude/skills"),
        ("codex", ".codex/skills"),
        ("cursor", ".cursor/skills"),
    }
    ctx = SkillSourceContext(
        roots=(),
        home=resolved_home,
        skills_filter="all",
        bundle_dir=None,
    )
    for spec in _claude_plugin_skills(ctx):
        if spec.skill_dir is None:
            continue
        rel = _rel_home_path(resolved_home, spec.skill_dir.parent)
        if rel is not None:
            roots.add(("claude", rel))
    return sorted(roots)


class SkillSyncRegistry:
    """Process-local skill manifest rebuilt at startup and on refresh."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = (home or Path.home()).resolve()
        self._entries: dict[str, SkillSyncEntry] = {}
        self.refresh()

    @property
    def home(self) -> Path:
        return self._home

    def refresh(self) -> None:
        """Re-scan disk and replace the in-memory manifest."""
        self._entries = build_skill_sync_manifest(self._home)

    def entries(self) -> list[SkillSyncEntry]:
        return sorted(self._entries.values(), key=lambda entry: entry.name)

    def get(self, name: str) -> SkillSyncEntry | None:
        return self._entries.get(name)

    def pop(self, name: str) -> SkillSyncEntry | None:
        return self._entries.pop(name, None)
