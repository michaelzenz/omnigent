"""Pi-compatible filesystem search operations for governed OS environments."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

OpResult = dict[str, Any]

MAX_OUTPUT_BYTES = 50 * 1024
GREP_MAX_LINE_CHARS = 500


def ripgrep_available() -> bool:
    try:
        _ripgrep()
    except RuntimeError:
        return False
    return True


def _ripgrep() -> str:
    executable = shutil.which("rg")
    if executable is not None:
        return executable
    managed_name = "rg.exe" if os.name == "nt" else "rg"
    managed = Path.home() / ".pi" / "agent" / "bin" / managed_name
    if managed.is_file() and os.access(managed, os.X_OK):
        return str(managed)
    raise RuntimeError(
        f"ripgrep is unavailable; install rg on PATH or provision Pi's managed binary at {managed}"
    )


def _truncate_head(text: str) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return text, False
    return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore"), True


def _display_path(raw_path: str, search_path: Path) -> str:
    candidate = Path(raw_path)
    if search_path.is_dir():
        try:
            return candidate.resolve().relative_to(search_path.resolve()).as_posix()
        except ValueError:
            pass
    return candidate.name


def grep(
    *,
    pattern: str,
    path: Path,
    glob: str | None,
    ignore_case: bool,
    literal: bool,
    context: int,
    limit: int,
) -> OpResult:
    """Run ripgrep with Pi-compatible ignore and truncation behavior."""
    if not path.exists():
        return {"error": f"Path not found: {path}"}
    if isinstance(context, bool) or not isinstance(context, int) or context < 0:
        return {"error": "context must be a non-negative integer"}
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return {"error": "limit must be a positive integer"}
    if glob is not None and not isinstance(glob, str):
        return {"error": "glob must be a string"}

    try:
        executable = _ripgrep()
    except RuntimeError as exc:
        return {"error": str(exc)}
    argv = [executable, "--json", "--line-number", "--color=never", "--hidden"]
    if ignore_case:
        argv.append("--ignore-case")
    if literal:
        argv.append("--fixed-strings")
    if glob:
        argv.extend(("--glob", glob))
    if context:
        argv.extend(("--context", str(context)))
    argv.extend(("--", pattern, str(path)))

    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return {"error": f"Failed to run ripgrep: {exc}"}

    output: list[str] = []
    matched_paths: set[str] = set()
    matches = 0
    lines_truncated = False
    stopped_for_limit = False
    assert process.stdout is not None
    for raw_event in process.stdout:
        try:
            event = json.loads(raw_event)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type not in {"match", "context"}:
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        if event_type == "match":
            if matches >= limit:
                stopped_for_limit = True
                process.terminate()
                break
            matches += 1
        path_data = data.get("path")
        lines_data = data.get("lines")
        line_number = data.get("line_number")
        if not isinstance(path_data, dict) or not isinstance(lines_data, dict):
            continue
        raw_name = path_data.get("text")
        raw_line = lines_data.get("text")
        if (
            not isinstance(raw_name, str)
            or not isinstance(raw_line, str)
            or isinstance(line_number, bool)
            or not isinstance(line_number, int)
        ):
            continue
        matched_paths.add(raw_name)
        text = raw_line.rstrip("\r\n")
        if len(text) > GREP_MAX_LINE_CHARS:
            text = text[:GREP_MAX_LINE_CHARS] + "…"
            lines_truncated = True
        separator = ":" if event_type == "match" else "-"
        output.append(f"{_display_path(raw_name, path)}{separator}{line_number}{separator} {text}")

    if stopped_for_limit:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    _, stderr = process.communicate()
    if not stopped_for_limit and process.returncode not in (0, 1):
        return {"error": stderr.strip() or f"ripgrep exited with code {process.returncode}"}
    if not output:
        return {"content": "No matches found", "count": 0, "truncated": False}

    content, byte_truncated = _truncate_head("\n".join(output))
    notices: list[str] = []
    if stopped_for_limit:
        notices.append(f"{limit} matches limit reached; refine the pattern or increase limit")
    if byte_truncated:
        notices.append("50KB output limit reached")
    if lines_truncated:
        notices.append("some lines truncated to 500 characters; use read for full lines")
    if notices:
        content += "\n\n[Truncated: " + ". ".join(notices) + "]"
    return {
        "content": content,
        "count": min(matches, limit),
        "truncated": bool(notices),
        "_matched_paths": sorted(matched_paths),
    }


def find(*, pattern: str, path: Path, limit: int) -> OpResult:
    """Use ripgrep's gitignore-aware enumeration for Pi's find contract."""
    if not path.exists():
        return {"error": f"Path not found: {path}"}
    if not path.is_dir():
        return {"error": f"Not a directory: {path}"}
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return {"error": "limit must be a positive integer"}
    try:
        executable = _ripgrep()
    except RuntimeError as exc:
        return {"error": str(exc)}
    argv = [
        executable,
        "--files",
        "--hidden",
        "--no-require-git",
        "--glob",
        pattern,
        "--",
        str(path),
    ]
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return {"error": f"Failed to run ripgrep: {exc}"}

    results: list[str] = []
    matched_paths: list[str] = []
    stopped_for_limit = False
    assert process.stdout is not None
    for raw_line in process.stdout:
        if len(results) >= limit:
            stopped_for_limit = True
            process.terminate()
            break
        candidate = Path(raw_line.rstrip("\r\n"))
        matched_paths.append(str(candidate))
        try:
            results.append(candidate.resolve().relative_to(path.resolve()).as_posix())
        except ValueError:
            continue
    if stopped_for_limit:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    _, stderr = process.communicate()
    if not stopped_for_limit and process.returncode not in (0, 1):
        return {"error": stderr.strip() or f"ripgrep exited with code {process.returncode}"}
    if not results:
        return {"content": "No files found matching pattern", "count": 0, "truncated": False}

    content, byte_truncated = _truncate_head("\n".join(results))
    notices: list[str] = []
    if stopped_for_limit:
        notices.append(f"{limit} results limit reached")
    if byte_truncated:
        notices.append("50KB output limit reached")
    if notices:
        content += "\n\n[Truncated: " + ". ".join(notices) + "]"
    return {
        "content": content,
        "count": len(results),
        "truncated": bool(notices),
        "_matched_paths": matched_paths,
    }


def list_dir(*, path: Path, limit: int) -> OpResult:
    """List one directory using Pi's deterministic formatting and limits."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return {"error": "limit must be a positive integer"}
    if not path.exists():
        return {"error": f"Path not found: {path}"}
    if not path.is_dir():
        return {"error": f"Not a directory: {path}"}
    try:
        names = sorted(os.listdir(path))
    except OSError as exc:
        return {"error": f"Failed to list directory: {exc}"}
    output: list[str] = []
    for name in names[:limit]:
        candidate = path / name
        try:
            output.append(name + ("/" if candidate.is_dir() else ""))
        except OSError:
            continue
    if not output:
        return {"content": "(empty directory)", "count": 0, "truncated": False}
    content, byte_truncated = _truncate_head("\n".join(output))
    notices: list[str] = []
    if len(names) > limit:
        notices.append(f"{limit} entries limit reached; increase limit for more")
    if byte_truncated:
        notices.append("50KB output limit reached")
    if notices:
        content += "\n\n[Truncated: " + ". ".join(notices) + "]"
    return {"content": content, "count": len(output), "truncated": bool(notices)}
