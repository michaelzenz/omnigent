from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from omnigent.inner.datamodel import OSEnvSpec
from omnigent.inner.os_env import OSEnvironment
from omnigent.runner.dynamic_file_memory import configure, discover


class _MemoryOSEnvironment(OSEnvironment):
    async def read(
        self,
        path: str,
        offset: int = 1,
        limit: int | None = None,
        max_binary_bytes: int | None = None,
    ) -> dict[str, Any]:
        del max_binary_bytes
        candidate = Path(path)
        if not candidate.exists():
            return {"error": "not found"}
        text = candidate.read_text()
        lines = text.splitlines(keepends=True)
        selected = lines[offset - 1 :] if limit is None else lines[offset - 1 : offset - 1 + limit]
        return {
            "path": str(candidate),
            "encoding": "utf-8",
            "content": "".join(selected),
            "offset": offset,
            "returned_lines": len(selected),
            "total_lines": len(lines),
        }

    async def write(self, path: str, content: str) -> dict[str, Any]:
        raise NotImplementedError

    async def edit(self, path: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    async def shell(
        self,
        command: str,
        timeout: int | None = None,
        max_output: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def discover_memory_files(
        self,
        paths: list[tuple[str, bool]],
        filename: str,
        *,
        max_bytes: int = 50 * 1024,
        offsets: dict[str, tuple[str, int]] | None = None,
    ) -> dict[str, Any]:
        del max_bytes
        offsets = offsets or {}
        candidates: list[Path] = []
        for raw_path, is_directory in paths:
            target = Path(raw_path)
            directory = target if is_directory and target.is_dir() else target.parent
            cursor = self.cwd
            candidates.append(cursor / filename)
            for part in directory.relative_to(self.cwd).parts:
                cursor /= part
                candidates.append(cursor / filename)
        files = []
        for candidate in dict.fromkeys(candidates):
            if candidate.is_file():
                content = candidate.read_text()
                state = offsets.get(str(candidate))
                start = state[1] if state is not None else 0
                files.append(
                    {
                        "path": str(candidate),
                        "sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "content": content.encode()[start:].decode(),
                        "start_byte": start,
                        "end_byte": len(content.encode()),
                        "truncated": False,
                    }
                )
        return {
            "files": files,
            "targets": [str(Path(path)) for path, _ in paths],
            "truncated": False,
        }


@pytest.mark.asyncio
async def test_discovers_nested_memory_once_and_preserves_specificity(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "pkg" / "src"
    nested.mkdir(parents=True)
    root_memory = tmp_path / "CLAUDE.md"
    package_memory = tmp_path / "pkg" / "CLAUDE.md"
    root_memory.write_text("root")
    package_memory.write_text("package")
    target = nested / "file.py"
    target.write_text("pass\n")
    root_hash = hashlib.sha256(b"root").hexdigest()
    env = _MemoryOSEnvironment(
        spec=OSEnvSpec(type="caller_process", cwd=str(tmp_path)),
        cwd=tmp_path,
    )
    configure(
        "conversation",
        {"provider": "claude", "files": [{"path": str(root_memory), "sha256": root_hash}]},
        [],
    )

    first = await discover(env, "conversation", [(str(target), False)])
    assert first is not None
    assert [item["path"] for item in first["files"]] == [str(package_memory)]
    assert await discover(env, "conversation", [(str(target), False)]) is None


@pytest.mark.asyncio
async def test_changed_memory_file_is_reinjected(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    memory = tmp_path / "CLAUDE.md"
    memory.write_text("before")
    env = _MemoryOSEnvironment(
        spec=OSEnvSpec(type="caller_process", cwd=str(tmp_path)),
        cwd=tmp_path,
    )
    configure(
        "conversation-changed",
        {
            "provider": "claude",
            "files": [
                {
                    "path": str(memory),
                    "sha256": hashlib.sha256(b"before").hexdigest(),
                }
            ],
        },
        [],
    )
    memory.write_text("after")
    discovered = await discover(env, "conversation-changed", [(str(tmp_path / "x.py"), False)])
    assert discovered is not None
    assert discovered["files"][0]["supersedes_previous_version"] is True
    assert discovered["files"][0]["instructions"] == "after"


@pytest.mark.asyncio
async def test_history_recovery_only_trusts_framework_tool_outputs(tmp_path: Path) -> None:
    memory = tmp_path / "AGENTS.md"
    memory.write_text("instructions")
    digest = hashlib.sha256(b"instructions").hexdigest()
    output = {
        "_omnigent_discovered_file_memory": {
            "provider": "AGENTS.md",
            "files": [{"path": str(memory), "sha256": digest}],
        },
        "content": "ok",
    }
    env = _MemoryOSEnvironment(
        spec=OSEnvSpec(type="caller_process", cwd=str(tmp_path)),
        cwd=tmp_path,
    )
    encoded = __import__("json").dumps(output)
    configure(
        "conversation-history",
        None,
        [
            {"type": "function_call", "name": "read", "call_id": "call-1"},
            {"type": "function_call_output", "call_id": "call-1", "output": encoded},
        ],
    )
    assert await discover(env, "conversation-history", [(str(tmp_path / "x.py"), False)]) is None

    configure(
        "conversation-user-spoof",
        {"provider": "agents", "files": []},
        [
            {"type": "function_call", "name": "untrusted_mcp", "call_id": "call-2"},
            {"type": "function_call_output", "call_id": "call-2", "output": encoded},
        ],
    )
    assert (
        await discover(env, "conversation-user-spoof", [(str(tmp_path / "x.py"), False)])
        is not None
    )
