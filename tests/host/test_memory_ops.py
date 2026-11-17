"""Tests for the memory operations that a host runs on its ``~/AGENTS.md``."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from omnigent.host.memory_ops import MAX_MEMORY_CHARS, handle_memory_fs_op


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_host_creates_reads_and_replaces_its_agents_md(tmp_path: Path) -> None:
    missing = handle_memory_fs_op("memory.read", {}, home=tmp_path)
    created = handle_memory_fs_op(
        "memory.write", {"content": "v1\n", "expected_sha256": None}, home=tmp_path
    )
    replaced = handle_memory_fs_op(
        "memory.write", {"content": "v2\n", "expected_sha256": _sha("v1\n")}, home=tmp_path
    )

    assert missing == {"exists": False, "content": "", "content_sha256": None}
    assert created == {"exists": True, "content": "v1\n", "content_sha256": _sha("v1\n")}
    assert replaced == {"exists": True, "content": "v2\n", "content_sha256": _sha("v2\n")}
    assert [path.name for path in tmp_path.iterdir()] == ["AGENTS.md"]


@pytest.mark.parametrize(
    ("on_host", "expected"),
    [
        ("edited on the host\n", None),
        ("edited on the host\n", _sha("read\n")),
        (None, _sha("read\n")),
    ],
)
def test_a_write_leaves_a_file_that_changed_after_the_read(
    tmp_path: Path, on_host: str | None, expected: str | None
) -> None:
    if on_host is not None:
        (tmp_path / "AGENTS.md").write_text(on_host, encoding="utf-8")

    with pytest.raises(FileExistsError):
        handle_memory_fs_op(
            "memory.write", {"content": "new\n", "expected_sha256": expected}, home=tmp_path
        )

    assert handle_memory_fs_op("memory.read", {}, home=tmp_path)["content"] == (on_host or "")


def test_a_write_of_the_text_that_the_file_already_has_succeeds(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("same\n", encoding="utf-8")

    result = handle_memory_fs_op(
        "memory.write", {"content": "same\n", "expected_sha256": _sha("old\n")}, home=tmp_path
    )

    assert result == {"exists": True, "content": "same\n", "content_sha256": _sha("same\n")}


def test_an_agents_md_that_is_not_a_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").mkdir()

    with pytest.raises(ValueError, match="not a regular file"):
        handle_memory_fs_op("memory.read", {}, home=tmp_path)


def test_a_write_through_a_link_keeps_the_link_and_the_file_mode(tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "AGENTS.md"
    real.parent.mkdir()
    real.write_text("v1\n", encoding="utf-8")
    real.chmod(0o600)
    home = tmp_path / "home"
    home.mkdir()
    (home / "AGENTS.md").symlink_to(real)

    handle_memory_fs_op(
        "memory.write", {"content": "v2\n", "expected_sha256": _sha("v1\n")}, home=home
    )

    assert (home / "AGENTS.md").is_symlink()
    assert real.read_text(encoding="utf-8") == "v2\n"
    assert real.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("op", "params", "message"),
    [
        ("memory.delete", {}, "unknown memory operation"),
        ("memory.write", {"content": 1, "expected_sha256": None}, "content must be text"),
        (
            "memory.write",
            {"content": "x" * (MAX_MEMORY_CHARS + 1), "expected_sha256": None},
            "longer than",
        ),
        ("memory.read", {}, "not UTF-8"),
    ],
)
def test_memory_ops_reject_bad_input(
    tmp_path: Path, op: str, params: dict[str, object], message: str
) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe")

    with pytest.raises(ValueError, match=message):
        handle_memory_fs_op(op, params, home=tmp_path)
