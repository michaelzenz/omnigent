"""On-demand CLAUDE.md/AGENTS.md discovery for explicit-path file tools."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from omnigent.inner.os_env import OSEnvironment

_PROVIDER_FILENAMES = {"claude": "CLAUDE.md", "agents": "AGENTS.md"}
_MARKER = "_omnigent_discovered_file_memory"
_MAX_DYNAMIC_MEMORY_BYTES = 50 * 1024
_TRUSTED_MEMORY_TOOLS = {
    "read",
    "write",
    "edit",
    "grep",
    "find",
    "ls",
    "sys_os_read",
    "sys_os_write",
    "sys_os_edit",
}


@dataclass
class _ConversationMemory:
    provider: str
    filename: str
    loaded: dict[str, str] = field(default_factory=dict)
    partial_states: dict[str, tuple[str, int]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_contexts: dict[str, _ConversationMemory] = {}


def _history_tool_outputs(value: Any) -> list[str]:
    """Return outputs correlated to trusted file-tool calls only."""
    if not isinstance(value, list):
        return []
    outputs: list[str] = []
    trusted_calls: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            if isinstance(call_id, str) and name in _TRUSTED_MEMORY_TOOLS:
                trusted_calls.add(call_id)
            continue
        if item_type == "function_call_output":
            call_id = item.get("call_id")
            output = item.get("output")
            if call_id in trusted_calls and isinstance(output, str):
                outputs.append(output)
            continue
        if item.get("role") in {"tool", "tool_result"}:
            name = item.get("name")
            content = item.get("content")
            if name in _TRUSTED_MEMORY_TOOLS and isinstance(content, str):
                outputs.append(content)
            continue
        for child in item.values():
            if isinstance(child, list):
                outputs.extend(_history_tool_outputs(child))
    return outputs


def _provider_from_history(history: Any) -> str | None:
    by_filename = {filename: provider for provider, filename in _PROVIDER_FILENAMES.items()}
    for output in _history_tool_outputs(history):
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            continue
        marker = decoded.get(_MARKER) if isinstance(decoded, dict) else None
        if isinstance(marker, dict) and marker.get("provider") in by_filename:
            return by_filename[marker["provider"]]
    return None


def configure(conversation_id: str, payload: Any, history: Any) -> None:
    """Seed current-turn memory provenance and recover prior tool discoveries."""
    if payload is None:
        if conversation_id in _contexts:
            return
        recovered_provider = _provider_from_history(history)
        if recovered_provider is None:
            return
        payload = {"provider": recovered_provider, "files": []}
    if not isinstance(payload, dict):
        _contexts.pop(conversation_id, None)
        return
    provider = payload.get("provider")
    filename = _PROVIDER_FILENAMES.get(provider)
    if filename is None:
        _contexts.pop(conversation_id, None)
        return
    context = _ConversationMemory(provider=provider, filename=filename)
    files = payload.get("files")
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            sha256 = item.get("sha256")
            if isinstance(path, str) and isinstance(sha256, str):
                context.loaded[path] = sha256
    for output in _history_tool_outputs(history):
        try:
            decoded = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            continue
        marker = decoded.get(_MARKER) if isinstance(decoded, dict) else None
        if not isinstance(marker, dict) or marker.get("provider") != filename:
            continue
        discovered = marker.get("files")
        if not isinstance(discovered, list):
            continue
        for item in discovered:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            sha256 = item.get("sha256")
            end_byte = item.get("end_byte")
            if not isinstance(path, str) or not isinstance(sha256, str):
                continue
            if item.get("truncated") is True and isinstance(end_byte, int):
                previous = context.partial_states.get(path)
                previous_offset = (
                    previous[1] if previous is not None and previous[0] == sha256 else 0
                )
                context.partial_states[path] = (sha256, max(previous_offset, end_byte))
            else:
                context.loaded[path] = sha256
                context.partial_states.pop(path, None)
    _contexts[conversation_id] = context


def clear(conversation_id: str) -> None:
    _contexts.pop(conversation_id, None)


async def discover(
    os_env: OSEnvironment,
    conversation_id: str | None,
    paths: list[tuple[str, bool]],
    *,
    omit_target_contents: bool = False,
    record: bool = True,
) -> dict[str, Any] | None:
    """Read newly applicable instruction files through the active OS environment."""
    if not conversation_id:
        return None
    context = _contexts.get(conversation_id)
    if context is None:
        return None
    async with context.lock:
        result = await os_env.discover_memory_files(
            paths,
            context.filename,
            max_bytes=_MAX_DYNAMIC_MEMORY_BYTES,
            offsets=context.partial_states,
        )
        if not isinstance(result, dict):
            raise RuntimeError("Memory discovery returned an invalid result")
        if result.get("error") is not None:
            raise RuntimeError(str(result["error"]))
        files = result.get("files")
        if not isinstance(files, list):
            raise RuntimeError("Memory discovery result omitted files")
        raw_targets = result.get("targets")
        targets = set(raw_targets) if isinstance(raw_targets, list) else set()
        discovered: list[dict[str, Any]] = []
        pending_loaded: dict[str, str] = {}
        pending_states: dict[str, tuple[str, int]] = {}
        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            sha256 = item.get("sha256")
            content = item.get("content")
            truncated = item.get("truncated") is True
            start_byte = item.get("start_byte")
            end_byte = item.get("end_byte")
            if not isinstance(path, str) or not isinstance(sha256, str):
                continue
            if not isinstance(content, str):
                raise RuntimeError(f"Applicable memory file is not UTF-8 text: {path}")
            if context.loaded.get(path) == sha256 or pending_loaded.get(path) == sha256:
                continue
            entry: dict[str, Any] = {
                "path": path,
                "sha256": sha256,
                "supersedes_previous_version": path in context.loaded,
                "truncated": truncated,
                "start_byte": start_byte,
                "end_byte": end_byte,
            }
            if not (omit_target_contents and path in targets):
                entry["instructions"] = content
            else:
                entry["instructions_in_read_result"] = True
            discovered.append(entry)
            if truncated and isinstance(end_byte, int):
                pending_states[path] = (sha256, end_byte)
            else:
                pending_loaded[path] = sha256
        if not discovered:
            return None
        if record:
            context.loaded.update(pending_loaded)
            context.partial_states.update(pending_states)
            for path in pending_loaded:
                context.partial_states.pop(path, None)
        return {
            "provider": context.filename,
            "instruction": (
                f"Read and follow these newly applicable {context.filename} files. "
                "Later, more specific files take precedence."
            ),
            "files": discovered,
            "truncated": result.get("truncated") is True,
        }


async def acknowledge(conversation_id: str | None, output: str) -> None:
    """Record discovered files only after their tool result reaches the harness."""
    if not conversation_id:
        return
    context = _contexts.get(conversation_id)
    if context is None:
        return
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError:
        return
    marker = decoded.get(_MARKER) if isinstance(decoded, dict) else None
    files = marker.get("files") if isinstance(marker, dict) else None
    if not isinstance(files, list):
        return
    async with context.lock:
        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            sha256 = item.get("sha256")
            end_byte = item.get("end_byte")
            if not isinstance(path, str) or not isinstance(sha256, str):
                continue
            if item.get("truncated") is True and isinstance(end_byte, int):
                previous = context.partial_states.get(path)
                previous_offset = (
                    previous[1] if previous is not None and previous[0] == sha256 else 0
                )
                context.partial_states[path] = (sha256, max(previous_offset, end_byte))
            else:
                context.loaded[path] = sha256
                context.partial_states.pop(path, None)


def attach(result: dict[str, Any], memory: dict[str, Any] | None) -> dict[str, Any]:
    if memory is None:
        return result
    return {_MARKER: memory, **result}
