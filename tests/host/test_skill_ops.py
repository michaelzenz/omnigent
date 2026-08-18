from __future__ import annotations

from pathlib import Path

from omnigent.host.skill_ops import handle_skill_fs_op, skill_inventory_wire


def _skill(home: Path, name: str, body: str = "body") -> Path:
    path = home / ".claude" / "skills" / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_host_discovers_and_edits_its_own_skills(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    skill = _skill(home, "demo")
    monkeypatch.setenv("HOME", str(home))

    inventory = skill_inventory_wire()
    assert inventory[0]["name"] == "demo"
    original_hash = inventory[0]["content_sha256"]

    read = handle_skill_fs_op(
        "skill.read",
        {"name": "demo", "rel_home_path": ".claude/skills/demo"},
    )
    assert "description: Demo" in read["content"]

    updated = read["content"].replace("body", "updated")
    result = handle_skill_fs_op(
        "skill.write",
        {
            "name": "demo",
            "rel_home_path": ".claude/skills/demo",
            "content": updated,
        },
    )
    assert skill.joinpath("SKILL.md").read_text(encoding="utf-8") == updated
    assert result["inventory"][0]["content_sha256"] != original_hash


def test_host_reports_search_roots_per_harness(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = handle_skill_fs_op("skill.roots", {})

    assert result["roots"] == [
        {"harness": "claude", "rel_home_path": ".claude/skills"},
        {"harness": "codex", "rel_home_path": ".codex/skills"},
        {"harness": "cursor", "rel_home_path": ".cursor/skills"},
    ]
    assert result["sync_harnesses"] == {
        "claude": True,
        "codex": True,
        "cursor": True,
    }

    updated = handle_skill_fs_op(
        "skill.settings",
        {"harness": "cursor", "enabled": False},
    )
    assert updated["sync_harnesses"]["cursor"] is False
    assert handle_skill_fs_op("skill.roots", {})["sync_harnesses"]["cursor"] is False


def test_host_preserves_symlinked_harness_path_in_inventory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    claude_skill = _skill(home, "shared")
    codex_root = home / ".codex" / "skills"
    codex_root.mkdir(parents=True)
    codex_root.joinpath("shared").symlink_to(claude_skill, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))

    inventory = skill_inventory_wire()

    codex = next(entry for entry in inventory if entry["harness"] == "codex")
    assert codex["rel_home_path"] == ".codex/skills/shared"


def test_host_reports_existing_omnigent_skill_as_optional_occurrence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _skill(home, "claude-only")
    omnigent = home / ".omnigent" / "skills" / "shared"
    omnigent.mkdir(parents=True)
    (omnigent / "SKILL.md").write_text(
        "---\nname: shared\ndescription: Omnigent copy\n---\n\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    inventory = skill_inventory_wire()

    assert any(
        entry["name"] == "shared"
        and entry["harness"] == "omnigent"
        and entry["rel_home_path"] == ".omnigent/skills/shared"
        for entry in inventory
    )
    assert not any(
        entry["name"] == "claude-only" and entry["harness"] == "omnigent"
        for entry in inventory
    )


def test_host_exports_and_imports_complete_skill_tree(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    skill = _skill(home, "demo")
    (skill / "references").mkdir()
    (skill / "references" / "guide.md").write_text("guide", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    exported = handle_skill_fs_op(
        "skill.export",
        {"name": "demo", "rel_home_path": ".claude/skills/demo"},
    )
    handle_skill_fs_op(
        "skill.import",
        {
            "rel_home_path": ".cursor/skills/demo",
            "files": exported["files"],
        },
    )
    assert (home / ".cursor/skills/demo/references/guide.md").read_text() == "guide"
