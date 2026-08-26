"""Pi-style coding tools backed by OmniHarness's governed OS environment.

The public names and broad contracts follow Pi 0.80.10's MIT-licensed native coding
tools. OmniHarness keeps execution inside :class:`OSEnvironment` so the same
tools work through every programmatic harness with the existing sandbox,
policy, and remote-host rules. ``read`` intentionally uses inclusive
``start``/``end`` fields instead of Pi's ``offset``/``limit`` fields.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from omnigent.inner.os_env import _DEFAULT_READ_LIMIT, OSEnvironment
from omnigent.inner.pi_file_ops import ripgrep_available
from omnigent.tools.base import Tool, ToolContext

_logger = logging.getLogger(__name__)


def _schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def _positive_int(value: Any, name: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _non_negative_int(value: Any, name: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _optional_bool(value: Any, name: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _required_string(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


class _PiFileTool(Tool):
    def __init__(self, os_env: OSEnvironment) -> None:
        self._os_env = os_env

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        del ctx
        try:
            values = json.loads(arguments) if arguments else {}
            if not isinstance(values, dict):
                raise ValueError("arguments must be a JSON object")
            result = asyncio.run(self._invoke_async(values))
            result.pop("_matched_paths", None)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            _logger.exception("%s failed", self.name())
            return json.dumps({"error": str(exc)})
        return json.dumps(result)

    async def _invoke_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class ReadTool(_PiFileTool):
    @classmethod
    def name(cls) -> str:
        return "read"

    @classmethod
    def description(cls) -> str:
        return (
            "Read a UTF-8 text file or selected inclusive line range. Content is "
            "line-numbered. Use start and end to avoid loading large files."
        )

    def get_schema(self) -> dict[str, Any]:
        return _schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute file path."},
                    "start": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "First line to return (1-indexed, inclusive).",
                    },
                    "end": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Last line to return (1-indexed, inclusive).",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    async def _invoke_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = _required_string(arguments, "path")
        start = _positive_int(arguments.get("start"), "start", default=1)
        raw_end = arguments.get("end")
        if raw_end is None:
            limit = _DEFAULT_READ_LIMIT
        else:
            end = _positive_int(raw_end, "end", default=start)
            if end < start:
                raise ValueError("end must be greater than or equal to start")
            limit = end - start + 1
        result = dict(await self._os_env.read(path=path, offset=start, limit=limit))
        if result.get("encoding") == "utf-8" and isinstance(result.get("content"), str):
            lines = result["content"].splitlines()
            result["content"] = "\n".join(
                f"{start + index:>6}\t{line}" for index, line in enumerate(lines)
            )
            if result.get("returned_lines", 0) < max(0, result.get("total_lines", 0) - start + 1):
                next_line = start + int(result.get("returned_lines", 0))
                result["continuation"] = f"Read again with start={next_line} to continue."
        return result


class WriteTool(_PiFileTool):
    @classmethod
    def name(cls) -> str:
        return "write"

    @classmethod
    def description(cls) -> str:
        return "Write a complete UTF-8 text file, creating parent directories as needed."

    def get_schema(self) -> dict[str, Any]:
        return _schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute file path."},
                    "content": {"type": "string", "description": "Complete replacement content."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )

    async def _invoke_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = _required_string(arguments, "path")
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        return dict(await self._os_env.write(path=path, content=content))


class EditTool(_PiFileTool):
    @classmethod
    def name(cls) -> str:
        return "edit"

    @classmethod
    def description(cls) -> str:
        return (
            "Apply one or more exact, non-overlapping text replacements atomically. "
            "Every oldText must match exactly once in the original file."
        )

    def get_schema(self) -> dict[str, Any]:
        return _schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute file path."},
                    "edits": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldText": {"type": "string"},
                                "newText": {"type": "string"},
                            },
                            "required": ["oldText", "newText"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["path", "edits"],
                "additionalProperties": False,
            },
        )

    async def _invoke_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = _required_string(arguments, "path")
        edits = arguments.get("edits")
        if not isinstance(edits, list) or not edits:
            raise ValueError("edits must be a non-empty array")
        return dict(
            await self._os_env.edit(
                path=path,
                edits=edits,
                original_coordinates=True,
            )
        )


class BashTool(_PiFileTool):
    @classmethod
    def name(cls) -> str:
        return "bash"

    @classmethod
    def description(cls) -> str:
        return (
            "Run a shell command in the OS environment and return stdout, stderr, and exit code."
        )

    def get_schema(self) -> dict[str, Any]:
        return _schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."},
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Timeout in seconds (default: 120).",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    async def _invoke_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = _required_string(arguments, "command")
        timeout = _positive_int(arguments.get("timeout"), "timeout", default=120)
        return dict(
            await self._os_env.shell(command=command, timeout=timeout, max_output=50 * 1024)
        )


class GrepTool(_PiFileTool):
    @classmethod
    def name(cls) -> str:
        return "grep"

    @classmethod
    def description(cls) -> str:
        return (
            "Search file contents and return matching paths and line numbers. Respects "
            ".gitignore and supports regex, literal matching, glob filters, and context."
        )

    def get_schema(self) -> dict[str, Any]:
        return _schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex or literal search pattern.",
                    },
                    "path": {"type": "string", "description": "File or directory (default: cwd)."},
                    "glob": {
                        "type": "string",
                        "description": "Optional file glob, such as **/*.py.",
                    },
                    "ignoreCase": {"type": "boolean", "description": "Case-insensitive search."},
                    "literal": {
                        "type": "boolean",
                        "description": "Treat pattern as literal text.",
                    },
                    "context": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Context lines before and after.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Maximum matches (default: 100).",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        )

    async def _invoke_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = _required_string(arguments, "pattern")
        path = arguments.get("path", ".")
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        glob = arguments.get("glob")
        if glob is not None and not isinstance(glob, str):
            raise ValueError("glob must be a string")
        return dict(
            await self._os_env.grep(
                pattern,
                path,
                glob=glob,
                ignore_case=_optional_bool(arguments.get("ignoreCase"), "ignoreCase"),
                literal=_optional_bool(arguments.get("literal"), "literal"),
                context=_non_negative_int(arguments.get("context"), "context", default=0),
                limit=_positive_int(arguments.get("limit"), "limit", default=100),
            )
        )


class FindTool(_PiFileTool):
    @classmethod
    def name(cls) -> str:
        return "find"

    @classmethod
    def description(cls) -> str:
        return (
            "Find files by glob pattern. Respects .gitignore and returns paths "
            "relative to the search root."
        )

    def get_schema(self) -> dict[str, Any]:
        return _schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob such as **/*.spec.ts."},
                    "path": {
                        "type": "string",
                        "description": "Directory to search (default: cwd).",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Maximum results (default: 1000).",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        )

    async def _invoke_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = _required_string(arguments, "pattern")
        path = arguments.get("path", ".")
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        return dict(
            await self._os_env.find(
                pattern,
                path,
                limit=_positive_int(arguments.get("limit"), "limit", default=1000),
            )
        )


class LsTool(_PiFileTool):
    @classmethod
    def name(cls) -> str:
        return "ls"

    @classmethod
    def description(cls) -> str:
        return "List one directory, including dotfiles. Directory names end with '/'."

    def get_schema(self) -> dict[str, Any]:
        return _schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list (default: cwd)."},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Maximum entries (default: 500).",
                    },
                },
                "additionalProperties": False,
            },
        )

    async def _invoke_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path", ".")
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        return dict(
            await self._os_env.list_dir(
                path,
                limit=_positive_int(arguments.get("limit"), "limit", default=500),
            )
        )


async def execute_pi_file_tool(
    tool_name: str,
    arguments: dict[str, Any],
    os_env: OSEnvironment,
) -> dict[str, Any]:
    """Execute one Pi-style tool with the same validation used by ToolManager."""
    for tool in build_pi_file_tools(os_env):
        if tool.name() == tool_name:
            assert isinstance(tool, _PiFileTool)
            return await tool._invoke_async(arguments)
    raise ValueError(f"Unknown Pi file tool: {tool_name}")


def build_pi_file_tools(os_env: OSEnvironment) -> list[Tool]:
    """Build the seven Pi-style coding tools for one OS environment."""
    tools: list[Tool] = [
        ReadTool(os_env),
        WriteTool(os_env),
        EditTool(os_env),
        BashTool(os_env),
    ]
    if ripgrep_available():
        tools.extend((GrepTool(os_env), FindTool(os_env)))
    tools.append(LsTool(os_env))
    return tools
