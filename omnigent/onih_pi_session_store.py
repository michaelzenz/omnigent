"""Disposable, canonical-rebuild session storage for the Onih Pi executor."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

from omnigent.pi_native_resume import (
    pi_resume_session_path,
    pi_session_records_from_session_items,
    write_pi_session_records,
)


def delete_onih_pi_session(conversation_id: str) -> None:
    """Remove host-local Onih Pi state for a deleted conversation."""
    from omnigent.process_logging import data_dir

    digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
    shutil.rmtree(data_dir() / "pi" / "onih" / "sessions" / digest, ignore_errors=True)


def ensure_shared_pi_config(
    config_root: Path,
    fingerprint: str,
    files: Mapping[str, str],
) -> Path:
    """Initialize or validate one immutable host-local Pi config directory."""
    config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_root.chmod(0o700)
    lock_path = config_root / f"{fingerprint}.lock"
    with lock_path.open("a+b") as lock:
        lock_path.chmod(0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        target = config_root / fingerprint
        if _shared_config_matches(target, files):
            return target

        staging = Path(tempfile.mkdtemp(prefix=f".{fingerprint}.staging-", dir=config_root))
        invalid = config_root / f".{fingerprint}.invalid-{os.getpid()}"
        try:
            staging.chmod(0o700)
            for relative_path, content in files.items():
                path = staging / relative_path
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                path.chmod(0o600)
            if not _shared_config_matches(staging, files):
                raise RuntimeError("staged Pi configuration failed validation")
            with contextlib.suppress(FileNotFoundError):
                shutil.rmtree(invalid)
            if target.exists():
                os.replace(target, invalid)
            try:
                os.replace(staging, target)
            except BaseException:
                if invalid.exists() and not target.exists():
                    os.replace(invalid, target)
                raise
            shutil.rmtree(invalid, ignore_errors=True)
            return target
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _shared_config_matches(target: Path, files: Mapping[str, str]) -> bool:
    if not target.is_dir():
        return False
    try:
        for relative_path, expected in files.items():
            path = target / relative_path
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                return False
        expected_paths = {target / relative_path for relative_path in files}
        actual_paths = {path for path in target.rglob("*") if path.is_file()}
        return actual_paths == expected_paths
    except OSError:
        return False


class OnihPiSessionStore:
    """Own per-conversation locks and atomic Pi session replacement."""

    def __init__(self, sessions_root: Path) -> None:
        self.sessions_root = sessions_root
        self._locks: dict[str, IO[bytes]] = {}

    @staticmethod
    def conversation_hash(conversation_id: str) -> str:
        return hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()

    def active_dir(self, conversation_id: str) -> Path:
        return self.sessions_root / self.conversation_hash(conversation_id) / "active"

    def acquire(self, conversation_id: str) -> None:
        if conversation_id in self._locks:
            return
        root = self.active_dir(conversation_id).parent
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        handle = (root / "lock").open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError(f"Onih Pi session is already owned: {conversation_id}") from None
        self._locks[conversation_id] = handle

    def release(self, conversation_id: str) -> None:
        handle = self._locks.pop(conversation_id, None)
        if handle is None:
            return
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def rebuild(
        self,
        *,
        conversation_id: str,
        pi_session_id: str,
        items: list[dict[str, Any]],
        workspace: Path,
        provider: str,
        model: str,
    ) -> Path:
        """Strictly convert canonical items into a staging directory."""
        self.acquire(conversation_id)
        items = self._validate_items(items)
        active = self.active_dir(conversation_id)
        root = active.parent
        staging = Path(tempfile.mkdtemp(prefix="staging-", dir=root))
        try:
            records = pi_session_records_from_session_items(
                items,
                session_id=conversation_id,
                external_session_id=pi_session_id,
                cwd=workspace,
                provider=provider,
                model=model,
            )
            target = pi_resume_session_path(staging, pi_session_id)
            write_pi_session_records(target, records)
            staging.chmod(0o700)
            return staging
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                shutil.rmtree(staging)
            raise

    def activate(self, conversation_id: str, staging: Path) -> Path:
        """Atomically install a Pi-validated staging directory."""
        active = self.active_dir(conversation_id)
        previous = active.parent / "previous-replacement"
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(previous)
        if active.exists():
            os.replace(active, previous)
        try:
            os.replace(staging, active)
        except BaseException:
            if previous.exists() and not active.exists():
                os.replace(previous, active)
            raise
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(previous)
        return active

    @staticmethod
    def _validate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        supported = {"message", "function_call", "function_call_output", "error"}
        calls: dict[str, str] = {}
        normalized: list[dict[str, Any]] = []
        for item in items:
            item_type = item.get("type")
            if item_type not in supported:
                raise ValueError(
                    f"unsupported canonical item for Pi reconstruction: {item_type!r}"
                )
            copied = dict(item)
            if item_type == "message":
                role = item.get("role")
                if role not in {"user", "assistant"}:
                    raise ValueError(f"unsupported canonical message role: {role!r}")
                content = item.get("content")
                if not isinstance(content, list) or not content:
                    raise ValueError("canonical message has no reconstructable content")
                expected_type = "input_text" if role == "user" else "output_text"
                for block in content:
                    if (
                        not isinstance(block, dict)
                        or block.get("type") != expected_type
                        or not isinstance(block.get("text"), str)
                    ):
                        raise ValueError(
                            f"unsupported canonical {role} content during Pi reconstruction"
                        )
            elif item_type == "function_call":
                call_id = item.get("call_id")
                name = item.get("name")
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("canonical function call is missing call_id")
                if not isinstance(name, str) or not name:
                    raise ValueError(f"canonical function call {call_id!r} is missing its name")
                calls[call_id] = name
            elif item_type == "function_call_output":
                call_id = item.get("call_id")
                if not isinstance(call_id, str) or call_id not in calls:
                    raise ValueError(f"unpaired canonical tool result: {call_id!r}")
                copied.setdefault("name", calls[call_id])
                copied.setdefault("tool_status", "success")
            # "error" items are transcript metadata (NON_CONTENT_ITEM_TYPES),
            # not model history; the record converter drops them.
            normalized.append(copied)
        return normalized

    def delete(self, conversation_id: str) -> None:
        self.release(conversation_id)
        shutil.rmtree(self.active_dir(conversation_id).parent, ignore_errors=True)

    def close(self) -> None:
        for conversation_id in list(self._locks):
            self.release(conversation_id)
