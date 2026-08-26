from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from omnigent.inner.pi_file_ops import find, grep, list_dir

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is required")


def test_grep_respects_gitignore_and_reports_context(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "kept.py").write_text("before\nneedle\nafter\n")
    (tmp_path / "ignored.py").write_text("needle\n")
    result = grep(
        pattern="needle",
        path=tmp_path,
        glob="*.py",
        ignore_case=False,
        literal=False,
        context=1,
        limit=100,
    )
    assert "kept.py:2: needle" in result["content"]
    assert "kept.py-1- before" in result["content"]
    assert "ignored.py" not in result["content"]


def test_grep_does_not_follow_symlinked_files(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret-marker\n")
    (tmp_path / "linked.txt").symlink_to(outside)
    result = grep(
        pattern="secret-marker",
        path=tmp_path,
        glob=None,
        ignore_case=False,
        literal=True,
        context=0,
        limit=100,
    )
    assert result["count"] == 0


def test_find_respects_gitignore_and_returns_relative_paths(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "kept.py").write_text("")
    (tmp_path / "ignored.py").write_text("")
    result = find(pattern="*.py", path=tmp_path, limit=100)
    assert result["content"] == "kept.py"


def test_find_and_ls_validate_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert "Path not found" in find(pattern="*", path=missing, limit=10)["error"]
    file_path = tmp_path / "file.txt"
    file_path.write_text("")
    assert "Not a directory" in list_dir(path=file_path, limit=10)["error"]


def test_ls_includes_dotfiles_and_marks_directories(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("")
    (tmp_path / "folder").mkdir()
    result = list_dir(path=tmp_path, limit=10)
    assert result["content"].splitlines() == [".env", "folder/"]
