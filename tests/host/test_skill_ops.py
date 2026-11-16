"""Tests for the skill sync operations that a host runs on its home skill folders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnigent.host.skill_ops import handle_skill_fs_op, skill_inventory_wire

DEMO = {"name": "demo", "rel_home_path": ".claude/skills/demo"}


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Sync settings live next to the Omnigent config; keep them out of the real one.
    monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(tmp_path / "config"))
    return home


def _skill(home: Path, name: str, body: str = "body", root: str = ".claude/skills") -> Path:
    path = home / root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo\n---\n\n{body}\n", encoding="utf-8"
    )
    return path


def test_host_discovers_and_edits_its_own_skills(home: Path) -> None:
    skill = _skill(home, "demo")

    inventory = skill_inventory_wire()
    read = handle_skill_fs_op("skill.read", DEMO)
    updated = read["content"].replace("body", "updated")
    result = handle_skill_fs_op("skill.write", {**DEMO, "content": updated})
    renamed = updated.replace("name: demo", "name: other")

    assert inventory[0]["name"] == "demo"
    assert "description: Demo" in read["content"]
    assert skill.joinpath("SKILL.md").read_text(encoding="utf-8") == updated
    assert result["inventory"][0]["content_sha256"] != inventory[0]["content_sha256"]
    with pytest.raises(ValueError, match="cannot be changed"):
        handle_skill_fs_op("skill.write", {**DEMO, "content": renamed})


def test_host_inventory_operation_rescans_new_skills(home: Path) -> None:
    empty = handle_skill_fs_op("skill.inventory", {})["inventory"]
    _skill(home, "new-skill")

    inventory = handle_skill_fs_op("skill.inventory", {})["inventory"]

    assert empty == []
    assert [entry["name"] for entry in inventory] == ["new-skill"]


def test_host_edits_any_text_file_but_rejects_binary_files(home: Path) -> None:
    skill = _skill(home, "demo")
    reference = skill / "references" / "tables.md"
    reference.parent.mkdir()
    reference.write_text("old", encoding="utf-8")
    (skill / "image.bin").write_bytes(b"\xff\xfe")

    handle_skill_fs_op(
        "skill.files.write",
        {
            **DEMO,
            "files": {
                "SKILL.md": "---\nname: demo\ndescription: Demo\n---\n\nupdated\n",
                "references/tables.md": "new",
            },
        },
    )

    assert "updated" in skill.joinpath("SKILL.md").read_text(encoding="utf-8")
    assert reference.read_text(encoding="utf-8") == "new"
    with pytest.raises(ValueError, match="binary"):
        handle_skill_fs_op(
            "skill.files.write", {**DEMO, "files": {"image.bin": "not binary anymore"}}
        )


def test_host_rejects_stale_variant_update_and_paths_outside_skill_folders(home: Path) -> None:
    _skill(home, "demo")

    with pytest.raises(ValueError, match="changed since"):
        handle_skill_fs_op(
            "skill.files.write",
            {**DEMO, "expected_content_sha256": "stale", "files": {"SKILL.md": "replacement"}},
        )
    for outside in ("Documents/demo", ".claude/../Documents/demo", "/etc/demo"):
        with pytest.raises(ValueError):
            handle_skill_fs_op("skill.delete", {"name": "demo", "rel_home_path": outside})
    with pytest.raises(FileNotFoundError):
        handle_skill_fs_op("skill.read", {"name": "gone", "rel_home_path": ".claude/skills/gone"})


def test_host_reports_search_roots_and_keeps_sync_settings_by_its_config(
    home: Path, tmp_path: Path
) -> None:
    result = handle_skill_fs_op("skill.roots", {})
    updated = handle_skill_fs_op("skill.settings", {"harness": "cursor", "enabled": False})

    assert result["roots"] == [
        {"harness": "claude", "rel_home_path": ".claude/skills"},
        {"harness": "codex", "rel_home_path": ".codex/skills"},
        {"harness": "cursor", "rel_home_path": ".cursor/skills"},
    ]
    assert result["sync_harnesses"] == {"claude": True, "codex": True, "cursor": True}
    assert updated["sync_harnesses"]["cursor"] is False
    assert handle_skill_fs_op("skill.roots", {})["sync_harnesses"]["cursor"] is False
    saved = json.loads((tmp_path / "config" / "skill-sync.json").read_text(encoding="utf-8"))
    assert saved["cursor"] is False
    with pytest.raises(ValueError, match="unknown skill harness"):
        handle_skill_fs_op("skill.settings", {"harness": "gemini", "enabled": False})


def test_host_preserves_symlinked_harness_path_in_inventory(home: Path) -> None:
    claude_skill = _skill(home, "shared")
    codex_root = home / ".codex" / "skills"
    codex_root.mkdir(parents=True)
    codex_root.joinpath("shared").symlink_to(claude_skill, target_is_directory=True)

    inventory = skill_inventory_wire()

    codex = next(entry for entry in inventory if entry["harness"] == "codex")
    assert codex["rel_home_path"] == ".codex/skills/shared"


def test_host_reports_an_omnigent_skill_unless_claude_has_it(home: Path) -> None:
    _skill(home, "claude-only")
    _skill(home, "claude-only", root=".agents/skills")
    _skill(home, "shared", root=".omnigent/skills")
    _skill(home, "agents", root=".agents/skills")

    omnigent = {
        (entry["name"], entry["rel_home_path"])
        for entry in skill_inventory_wire()
        if entry["harness"] == "omnigent"
    }

    assert omnigent == {
        ("shared", ".omnigent/skills/shared"),
        ("agents", ".agents/skills/agents"),
    }


def test_host_exports_and_imports_complete_skill_tree(home: Path) -> None:
    skill = _skill(home, "demo")
    (skill / "references").mkdir()
    (skill / "references" / "guide.md").write_text("guide", encoding="utf-8")

    exported = handle_skill_fs_op("skill.export", DEMO)
    handle_skill_fs_op(
        "skill.import", {"rel_home_path": ".cursor/skills/demo", "files": exported["files"]}
    )

    assert (home / ".cursor/skills/demo/references/guide.md").read_text() == "guide"
    with pytest.raises(FileExistsError):
        handle_skill_fs_op(
            "skill.import",
            {
                "rel_home_path": ".cursor/skills/demo",
                "files": exported["files"],
                "create_only": True,
            },
        )


def test_host_import_validates_requested_skill_name(home: Path) -> None:
    _skill(home, "other")
    exported = handle_skill_fs_op(
        "skill.export", {"name": "other", "rel_home_path": ".claude/skills/other"}
    )

    with pytest.raises(ValueError, match="frontmatter name"):
        handle_skill_fs_op(
            "skill.import",
            {"name": "demo", "rel_home_path": ".cursor/skills/demo", "files": exported["files"]},
        )
    assert not (home / ".cursor/skills/demo").exists()
