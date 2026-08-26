from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnigent.inner.datamodel import OSEnvSpec
from omnigent.inner.os_env import CallerProcessOSEnvironment, create_os_environment
from omnigent.tools.base import ToolContext
from omnigent.tools.builtins.pi_file_tools import EditTool, ReadTool


@pytest.fixture
def os_env(tmp_path: Path) -> CallerProcessOSEnvironment:
    environment = create_os_environment(OSEnvSpec(type="caller_process", cwd=str(tmp_path)))
    assert isinstance(environment, CallerProcessOSEnvironment)
    yield environment
    environment.close()


def _context(tmp_path: Path) -> ToolContext:
    return ToolContext(task_id="task", agent_id="agent", workspace=tmp_path)


def test_read_uses_inclusive_start_end_and_line_numbers(
    os_env: CallerProcessOSEnvironment,
    tmp_path: Path,
) -> None:
    (tmp_path / "sample.txt").write_text("one\ntwo\nthree\nfour\n")
    result = json.loads(
        ReadTool(os_env).invoke(
            json.dumps({"path": "sample.txt", "start": 2, "end": 3}),
            _context(tmp_path),
        )
    )
    assert result["content"] == "     2\ttwo\n     3\tthree"
    assert result["returned_lines"] == 2


@pytest.mark.parametrize(
    "arguments,error",
    [
        ({"path": "sample.txt", "start": 0}, "start must be a positive integer"),
        ({"path": "sample.txt", "start": True}, "start must be a positive integer"),
        ({"path": "sample.txt", "start": 3, "end": 2}, "end must be greater"),
    ],
)
def test_read_rejects_invalid_ranges(
    os_env: CallerProcessOSEnvironment,
    tmp_path: Path,
    arguments: dict[str, object],
    error: str,
) -> None:
    (tmp_path / "sample.txt").write_text("one\ntwo\nthree\n")
    result = json.loads(ReadTool(os_env).invoke(json.dumps(arguments), _context(tmp_path)))
    assert error in result["error"]


def test_edit_batch_uses_original_coordinates_and_is_atomic(
    os_env: CallerProcessOSEnvironment,
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("alpha beta gamma")
    result = json.loads(
        EditTool(os_env).invoke(
            json.dumps(
                {
                    "path": "sample.txt",
                    "edits": [
                        {"oldText": "alpha", "newText": "alpha beta"},
                        {"oldText": "beta", "newText": "B"},
                    ],
                }
            ),
            _context(tmp_path),
        )
    )
    assert result["replacements"] == 2
    assert path.read_text() == "alpha beta B gamma"


def test_edit_rejects_overlapping_original_ranges_without_writing(
    os_env: CallerProcessOSEnvironment,
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("abcdef")
    result = json.loads(
        EditTool(os_env).invoke(
            json.dumps(
                {
                    "path": "sample.txt",
                    "edits": [
                        {"oldText": "abcd", "newText": "x"},
                        {"oldText": "cdef", "newText": "y"},
                    ],
                }
            ),
            _context(tmp_path),
        )
    )
    assert "overlap" in result["error"]
    assert path.read_text() == "abcdef"
