"""Memory operations that the server asks a host to run on its ``~/AGENTS.md``.

The server reads with ``host.fs_request`` (``MEMORY_READ_OPS``) and writes with
``host.fs_write_request`` (``MEMORY_WRITE_OPS``). The operations touch only the
``AGENTS.md`` file in the home directory, or the file that it links to.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import stat
import threading
from pathlib import Path
from typing import Any

MEMORY_READ_OPS = frozenset({"memory.read"})
MEMORY_WRITE_OPS = frozenset({"memory.write"})
MEMORY_FILE_NAME = "AGENTS.md"
MAX_MEMORY_CHARS = 1_000_000
_MAX_MEMORY_BYTES = MAX_MEMORY_CHARS * 4
_WRITE_LOCK = threading.Lock()


def handle_memory_fs_op(
    op: str, params: dict[str, Any], home: Path | None = None
) -> dict[str, Any]:
    """Run one memory operation on ``AGENTS.md`` in the home directory.

    ``memory.read`` returns the file. ``memory.write`` replaces it with
    ``params["content"]`` only while its hash is still
    ``params["expected_sha256"]``; ``None`` expects no file.

    :param op: ``"memory.read"`` or ``"memory.write"``.
    :param params: The operation's arguments.
    :param home: The home directory; ``None`` uses the current user's.
    :returns: The file state: ``exists``, ``content`` and ``content_sha256``.
    :raises FileExistsError: When the file changed since the caller read it.
    :raises ValueError: For an unknown operation, invalid arguments, or a file
        that is not regular UTF-8 text.
    """
    path = (home or Path.home()) / MEMORY_FILE_NAME
    if op == "memory.read":
        return read_memory_file(path)
    if op == "memory.write":
        content = params.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be text")
        expected = params.get("expected_sha256")
        if expected is not None and not isinstance(expected, str):
            raise ValueError("expected_sha256 must be text or null")
        write_memory_file(path, content, expected_sha256=expected)
        return read_memory_file(path)
    raise ValueError(f"unknown memory operation: {op}")


def read_memory_file(path: Path) -> dict[str, Any]:
    """Return the file's text and hash; a missing file has no hash.

    :param path: The ``AGENTS.md`` file.
    :returns: ``exists``, ``content`` and ``content_sha256``.
    :raises ValueError: When the file is not regular UTF-8 text or is too large.
    """
    data = _read_bytes(path)
    if data is None:
        return {"exists": False, "content": "", "content_sha256": None}
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{MEMORY_FILE_NAME} is not UTF-8 text") from exc
    if len(content) > MAX_MEMORY_CHARS:
        raise ValueError(f"{MEMORY_FILE_NAME} is longer than {MAX_MEMORY_CHARS} characters")
    return {"exists": True, "content": content, "content_sha256": _sha256(data)}


def write_memory_file(path: Path, content: str, *, expected_sha256: str | None) -> None:
    """Replace the file while its hash is still ``expected_sha256``.

    A file that already has ``content`` stays as it is. The new text goes to a
    temporary file next to the target first, so a failed write leaves the old
    file whole. A link keeps pointing at its target.

    :param path: The ``AGENTS.md`` file.
    :param content: The new text.
    :param expected_sha256: The hash that the caller read; ``None`` expects no file.
    :raises FileExistsError: When the file no longer has ``expected_sha256``.
    :raises ValueError: When the text is too long or the file is not regular.
    """
    if len(content) > MAX_MEMORY_CHARS:
        raise ValueError(f"{MEMORY_FILE_NAME} is longer than {MAX_MEMORY_CHARS} characters")
    encoded = content.encode("utf-8")
    with _WRITE_LOCK:
        target = path.resolve()
        current = _read_bytes(target)
        if current == encoded:
            return
        if (None if current is None else _sha256(current)) != expected_sha256:
            raise FileExistsError(
                f"{MEMORY_FILE_NAME} changed on this host; reload it and try again"
            )
        mode = target.stat().st_mode & 0o777 if current is not None else None
        temp = target.parent / f".{MEMORY_FILE_NAME}.{secrets.token_hex(8)}"
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is not None:
                os.chmod(temp, mode)
            os.replace(temp, target)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)
            raise


def _read_bytes(path: Path) -> bytes | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{MEMORY_FILE_NAME} is not a regular file")
    if info.st_size > _MAX_MEMORY_BYTES:
        raise ValueError(f"{MEMORY_FILE_NAME} is longer than {MAX_MEMORY_CHARS} characters")
    return path.read_bytes()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
