"""Host-local global memory file discovery and mutation."""

from __future__ import annotations

import hashlib
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

from omnigent.memory import (
    MEMORY_PROVIDER_GLOBAL_PATHS,
    MEMORY_PROVIDER_PROJECT_FILENAMES,
    MemoryProvider,
)

_FILE_PROVIDERS = frozenset({"claude", "agents"})
_MAX_MEMORY_FILE_BYTES = 2 * 1024 * 1024
_PATH_LOCKS: dict[Path, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.Lock:
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(path, threading.Lock())


def _provider(raw: object) -> MemoryProvider:
    if raw not in _FILE_PROVIDERS:
        raise ValueError("memory file provider must be claude or agents")
    return cast(MemoryProvider, raw)


def _global_path(provider: MemoryProvider, home: Path) -> Path:
    path = home / MEMORY_PROVIDER_GLOBAL_PATHS[provider]
    if path.is_symlink():
        raise ValueError("global memory file symlinks cannot be edited")
    resolved = path.resolve()
    resolved.relative_to(home.resolve())
    return resolved


def _read_text(path: Path) -> str | None:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size > _MAX_MEMORY_FILE_BYTES:
        raise ValueError("global memory file exceeds the 2 MiB limit")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("global memory file must be UTF-8 text") from exc


def _content_hash(content: str | None) -> str | None:
    return hashlib.sha256(content.encode()).hexdigest() if content is not None else None


def global_memory_file_wire(
    provider: MemoryProvider,
    home: Path | None = None,
) -> dict[str, object]:
    """Return one provider's global file content and content hash."""
    resolved_home = (home or Path.home()).resolve()
    content = _read_text(_global_path(provider, resolved_home))
    return {
        "provider": provider,
        "rel_home_path": MEMORY_PROVIDER_GLOBAL_PATHS[provider],
        "exists": content is not None,
        "content": content or "",
        "content_sha256": _content_hash(content),
    }


def global_memory_inventory_wire(home: Path | None = None) -> list[dict[str, object]]:
    """Return both supported global memory files for host inventory."""
    return [
        global_memory_file_wire(provider, home)
        for provider in (cast(MemoryProvider, "claude"), cast(MemoryProvider, "agents"))
    ]


def _project_root(workspace: Path) -> Path:
    for candidate in (workspace, *workspace.parents):
        if (candidate / ".git").exists():
            return candidate
    return workspace


def _project_memory_files(
    provider: MemoryProvider,
    workspace: str,
) -> list[dict[str, str]]:
    if not workspace:
        return []
    current = Path(workspace).expanduser().resolve()
    if not current.is_dir():
        raise FileNotFoundError("session workspace does not exist on host")
    root = _project_root(current)
    relative = current.relative_to(root)
    directories = [root]
    cursor = root
    for part in relative.parts:
        cursor /= part
        directories.append(cursor)
    filename = MEMORY_PROVIDER_PROJECT_FILENAMES[provider]
    files: list[dict[str, str]] = []
    for directory in directories:
        path = directory / filename
        content = _read_text(path)
        if content is not None:
            files.append({"path": str(path), "content": content})
    return files


def _write_global_file(
    provider: MemoryProvider,
    content: str,
    expected_sha256: str | None,
    *,
    home: Path,
) -> dict[str, object]:
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_MEMORY_FILE_BYTES:
        raise ValueError("global memory file exceeds the 2 MiB limit")
    destination = _global_path(provider, home)
    with _path_lock(destination):
        current = _read_text(destination)
        if _content_hash(current) != expected_sha256:
            raise FileExistsError("global memory file changed since it was loaded")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.omnigent.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            handle.write(encoded)
            temp = Path(handle.name)
        try:
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)
    return global_memory_file_wire(provider, home)


def handle_memory_fs_op(
    op: str,
    params: dict[str, Any],
    *,
    workspace: str = "",
) -> dict[str, Any]:
    """Execute a server-requested memory file operation on this host."""
    home = Path.home().resolve()
    provider = _provider(params.get("provider"))
    if op == "memory.file.read":
        return dict(global_memory_file_wire(provider, home))
    if op == "memory.file.write":
        content = params.get("content")
        expected = params.get("expected_sha256")
        if not isinstance(content, str) or not (expected is None or isinstance(expected, str)):
            raise ValueError("memory file write requires content and expected_sha256")
        return dict(
            _write_global_file(
                provider,
                content,
                expected,
                home=home,
            )
        )
    if op == "memory.project.read":
        global_file = global_memory_file_wire(provider, home)
        return {
            "provider": provider,
            "global_file": global_file,
            "project_files": _project_memory_files(provider, workspace),
        }
    raise ValueError(f"unsupported memory operation: {op}")
