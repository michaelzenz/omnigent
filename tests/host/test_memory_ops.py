from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.host.memory_ops import handle_memory_fs_op


@pytest.fixture()
def host_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_global_memory_file_write_is_conflict_safe(host_home: Path) -> None:
    missing = handle_memory_fs_op(
        "memory.file.read",
        {"provider": "agents"},
    )
    assert missing["exists"] is False
    assert missing["content_sha256"] is None

    created = handle_memory_fs_op(
        "memory.file.write",
        {
            "provider": "agents",
            "content": "Use concise answers.",
            "expected_sha256": None,
        },
    )
    assert created["exists"] is True
    assert created["content"] == "Use concise answers."
    assert (host_home / "AGENTS.md").read_text() == "Use concise answers."

    with pytest.raises(FileExistsError):
        handle_memory_fs_op(
            "memory.file.write",
            {
                "provider": "agents",
                "content": "Stale overwrite",
                "expected_sha256": None,
            },
        )

    with pytest.raises(ValueError, match="2 MiB"):
        handle_memory_fs_op(
            "memory.file.write",
            {
                "provider": "agents",
                "content": "x" * (2 * 1024 * 1024 + 1),
                "expected_sha256": created["content_sha256"],
            },
        )
    assert (host_home / "AGENTS.md").read_text() == "Use concise answers."


def test_project_memory_read_orders_global_to_working_directory(host_home: Path) -> None:
    (host_home / "CLAUDE.md").write_text("global")
    project = host_home / "project"
    nested = project / "packages" / "api"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "CLAUDE.md").write_text("root")
    (project / "packages" / "CLAUDE.md").write_text("package")
    (nested / "CLAUDE.md").write_text("api")

    payload = handle_memory_fs_op(
        "memory.project.read",
        {"provider": "claude"},
        workspace=str(nested),
    )

    assert payload["global_file"]["content"] == "global"
    assert [item["content"] for item in payload["project_files"]] == [
        "root",
        "package",
        "api",
    ]
