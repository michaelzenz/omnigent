"""Find the home skill folders that each harness uses on this host, for skill sync.

Each occurrence is the copy of a skill that one harness loads from the user's
home directory, with a hash of its whole folder, so the server can compare the
copies across hosts. Workspace skills are left out.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from omnigent.spec.parser import _discover_skills, _parse_skill
from omnigent.spec.skill_sources import SkillSourceContext, _claude_plugin_skills
from omnigent.spec.types import SkillSpec

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillSyncEntry:
    """
    One skill folder under the home directory.

    :param name: The skill's name, e.g. ``"pdf"``; a plugin skill is ``"<plugin>:<skill>"``.
    :param rel_home_path: The folder relative to the home directory, e.g.
        ``".claude/skills/pdf"``. A symlinked folder keeps its link path.
    :param abs_path: The resolved folder.
    :param content_sha256: The hash of the folder's file paths and bytes.
    :param description: The description in the skill's ``SKILL.md``.
    """

    name: str
    rel_home_path: str
    abs_path: Path
    content_sha256: str
    description: str


def skill_dir_sha256(skill_dir: Path) -> str:
    """Return the hash of a skill folder: each file's relative path and bytes, in path order."""
    digest = hashlib.sha256()
    if not skill_dir.is_dir():
        return digest.hexdigest()
    for file_path in sorted(path for path in skill_dir.rglob("*") if path.is_file()):
        digest.update(file_path.relative_to(skill_dir).as_posix().encode())
        digest.update(file_path.read_bytes())
    return digest.hexdigest()


def build_skill_occurrences(home: Path | None = None) -> list[tuple[str, SkillSyncEntry]]:
    """
    Return the skill copy that each harness loads from the home directory.

    Claude reads ``~/.claude/skills`` and its plugin skills, Cursor reads
    ``~/.cursor/skills`` and Codex reads its own skill sources. The generic
    ``omnigent`` lookup reads ``~/.agents/skills`` after ``~/.claude`` and lets
    ``~/.omnigent/skills`` override both.

    :param home: The home directory; ``None`` uses the current user's.
    :returns: ``(harness, entry)`` pairs sorted by skill name, then harness.
    """
    resolved_home = (home or Path.home()).resolve()
    skipped: list[str] = []
    claude = _scan_skills_dir(resolved_home / ".claude" / "skills", resolved_home, skipped=skipped)
    for spec in _claude_plugin_specs(resolved_home):
        if spec.skill_dir is not None and (
            entry := _entry(resolved_home, spec.name, spec.skill_dir, spec.description)
        ):
            claude[spec.name] = entry
    occurrences = [("claude", entry) for entry in claude.values()]
    occurrences += [
        ("cursor", entry) for entry in _scan_cursor_skills(resolved_home, skipped=skipped).values()
    ]
    occurrences += [
        ("codex", entry) for entry in _scan_codex_skills(resolved_home, skipped=skipped).values()
    ]
    generic = _scan_skills_dir(
        resolved_home / ".agents" / "skills", resolved_home, skipped=skipped
    )
    # The Claude occurrences already stand for the ~/.claude copies that win over ~/.agents.
    for name in set(generic) & set(claude):
        generic.pop(name)
    generic.update(
        _scan_skills_dir(resolved_home / ".omnigent" / "skills", resolved_home, skipped=skipped)
    )
    occurrences += [("omnigent", entry) for entry in generic.values()]
    for detail in skipped:
        _logger.warning("Skill inventory scan skipped: %s", detail)
    return sorted(occurrences, key=lambda item: (item[1].name, item[0]))


def build_skill_search_roots(home: Path | None = None) -> list[tuple[str, str]]:
    """
    Return every home folder that a harness searches for skills.

    :param home: The home directory; ``None`` uses the current user's.
    :returns: Sorted ``(harness, rel_home_path)`` pairs, e.g. ``("claude", ".claude/skills")``.
    """
    resolved_home = (home or Path.home()).resolve()
    roots = {
        ("claude", ".claude/skills"),
        ("codex", ".codex/skills"),
        ("cursor", ".cursor/skills"),
    }
    for spec in _claude_plugin_specs(resolved_home):
        if spec.skill_dir is not None and (
            rel := _rel_home_path(resolved_home, spec.skill_dir.parent)
        ):
            roots.add(("claude", rel))
    return sorted(roots)


def _rel_home_path(home: Path, skill_dir: Path) -> str | None:
    try:
        rel = Path(os.path.abspath(skill_dir)).relative_to(home)
    except ValueError:
        return None
    return rel.as_posix()


def _entry(home: Path, name: str, skill_dir: Path, description: str) -> SkillSyncEntry | None:
    rel = _rel_home_path(home, skill_dir)
    if rel is None:
        return None
    return SkillSyncEntry(
        name=name,
        rel_home_path=rel,
        abs_path=skill_dir.resolve(),
        content_sha256=skill_dir_sha256(skill_dir),
        description=description,
    )


def _claude_plugin_specs(home: Path) -> list[SkillSpec]:
    ctx = SkillSourceContext(roots=(), home=home, skills_filter="all", bundle_dir=None)
    return _claude_plugin_skills(ctx)


def _scan_skills_dir(
    skills_dir: Path, home: Path, *, skipped: list[str]
) -> dict[str, SkillSyncEntry]:
    out: dict[str, SkillSyncEntry] = {}
    if not skills_dir.is_dir():
        return out
    for spec in _discover_skills(skills_dir, skipped=skipped):
        if spec.skill_dir is not None and (
            entry := _entry(home, spec.name, spec.skill_dir, spec.description)
        ):
            out[spec.name] = entry
    return out


def _scan_codex_skills(home: Path, *, skipped: list[str]) -> dict[str, SkillSyncEntry]:
    from omnigent.inner.codex_executor import codex_skill_sources, select_codex_skill_dirs

    sources = codex_skill_sources(None, home)
    out: dict[str, SkillSyncEntry] = {}
    for name, skill_dir in select_codex_skill_dirs(
        "all", [*sources.linked, *sources.native]
    ).items():
        spec = _parsed(skill_dir / "SKILL.md", skipped=skipped)
        if spec is not None and (entry := _entry(home, name, skill_dir, spec.description)):
            out[name] = entry
    return out


def _scan_cursor_skills(home: Path, *, skipped: list[str]) -> dict[str, SkillSyncEntry]:
    skills_dir = home / ".cursor" / "skills"
    out: dict[str, SkillSyncEntry] = {}
    if not skills_dir.is_dir():
        return out
    try:
        children = sorted(skills_dir.iterdir())
    except OSError as exc:
        skipped.append(f"{skills_dir}: {type(exc).__name__}")
        return out
    for child in children:
        if not child.is_dir():
            continue
        spec = _parsed(child / "SKILL.md", skipped=skipped)
        if spec is not None and (entry := _entry(home, child.name, child, spec.description)):
            out[child.name] = entry
    return out


def _parsed(skill_md: Path, *, skipped: list[str]) -> SkillSpec | None:
    if not skill_md.is_file():
        return None
    try:
        return _parse_skill(skill_md)
    except Exception as exc:  # noqa: BLE001 — one unreadable skill must not hide the others
        # The type alone: a parse error can quote the file's text.
        skipped.append(f"{skill_md}: {type(exc).__name__}")
        return None
