"""Pi-compatible file-interaction tools backed by OSEnvironment.

These seven tools (``read``, ``write``, ``edit``, ``bash``, ``grep``,
``find``, ``ls``) mirror the native coding tools Pi exposes, but run
through OmniHarness's governed :class:`OSEnvironment` abstraction so
they are sandboxed, policy-gated, and portable across local, remote,
and sandboxed environments.

The legacy ``sys_os_*`` tools remain available alongside these; they
can be disabled later without affecting the new tools.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from omnigent.inner.os_env import OSEnvironment
from omnigent.tools.base import Tool, ToolContext

_logger = logging.getLogger(__name__)


# ── JSON Schemas (modeled after Pi's native tool interfaces) ────


_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to the file to read. ALWAYS prioritize this tool to read file."
            ),
        },
        "start": {
            "type": "integer",
            "description": "1-indexed starting line (inclusive). Defaults to 1.",
        },
        "end": {
            "type": "integer",
            "description": (
                "1-indexed ending line (inclusive). If omitted, reads up to "
                "2000 lines from start."
            ),
        },
    },
    "required": ["path"],
}

_WRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to write.",
        },
        "content": {
            "type": "string",
            "description": "Full file contents to write.",
        },
    },
    "required": ["path", "content"],
}

_EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to edit.",
        },
        "oldText": {
            "type": "string",
            "description": "Exact text to replace.",
        },
        "newText": {
            "type": "string",
            "description": "Replacement text.",
        },
        "edits": {
            "type": "array",
            "description": "Optional batch of exact edits.",
            "items": {
                "type": "object",
                "properties": {
                    "oldText": {"type": "string"},
                    "newText": {"type": "string"},
                },
                "required": ["oldText", "newText"],
            },
        },
    },
    "required": ["path"],
}

_BASH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Shell command to execute.",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds. Defaults to 120.",
        },
    },
    "required": ["command"],
}

_GREP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Regular expression (or literal if literal=true) to search for.",
        },
        "path": {
            "type": "string",
            "description": "File or directory to search in. Defaults to current directory.",
        },
        "glob": {
            "type": "string",
            "description": "Glob pattern to filter files, e.g. '*.py'.",
        },
        "ignoreCase": {
            "type": "boolean",
            "description": "Case-insensitive matching. Defaults to false.",
        },
        "literal": {
            "type": "boolean",
            "description": "Treat pattern as a literal string, not regex. Defaults to false.",
        },
        "context": {
            "type": "integer",
            "description": "Lines of context before and after each match. Defaults to 0.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of matches to return. Defaults to 100.",
        },
    },
    "required": ["pattern"],
}

_FIND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Glob pattern to match, e.g. '**/*.test.ts'.",
        },
        "path": {
            "type": "string",
            "description": "Directory to search in. Defaults to current directory.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of results. Defaults to 1000.",
        },
    },
    "required": ["pattern"],
}

_LS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Directory to list. Defaults to current directory.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of entries. Defaults to 500.",
        },
    },
    "required": [],
}


class _PiFileTool(Tool):
    """Base class shared by the Pi-compatible file-interaction tools."""

    def __init__(self, os_env: OSEnvironment) -> None:
        self._os_env = os_env

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        del ctx
        try:
            kwargs = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"malformed arguments JSON: {exc}"})
        if not isinstance(kwargs, dict):
            return json.dumps({"error": "arguments must be a JSON object"})
        import asyncio

        try:
            result = asyncio.run(self._invoke_async(kwargs))
        except Exception as exc:
            _logger.exception("%s failed", self.name())
            return json.dumps({"error": str(exc)})
        return json.dumps(result)

    async def _invoke_async(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class ReadTool(_PiFileTool):
    """``read`` — read a text file with inclusive start/end line range."""

    @classmethod
    def name(cls) -> str:
        return "read"

    @classmethod
    def description(cls) -> str:
        return (
            "Read a text file from the filesystem. Use start and end to read "
            "specific line ranges (1-indexed, inclusive). Returns line-numbered "
            "content. If the file is truncated, a note indicates how to read more."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": _READ_SCHEMA,
            },
        }

    async def _invoke_async(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        from omnigent.inner.os_env import _DEFAULT_READ_LIMIT

        start = kwargs.get("start", 1)
        end = kwargs.get("end")
        # Convert start/end to offset/limit for the OSEnvironment backend
        offset = start if isinstance(start, int) and start >= 1 else 1
        if end is not None and isinstance(end, int) and end >= offset:
            limit = end - offset + 1
        else:
            limit = _DEFAULT_READ_LIMIT
        result = dict(
            await self._os_env.read(
                path=kwargs["path"],
                offset=offset,
                limit=limit,
            )
        )
        # Add line-numbered content for better model usability
        if "content" in result and "encoding" not in result:
            lines = result["content"].splitlines()
            numbered = "\n".join(
                f"{result['offset'] + i:>6}\t{line}" for i, line in enumerate(lines)
            )
            result["content"] = numbered
        return result


class WriteTool(_PiFileTool):
    """``write`` — write a full file, creating parent directories."""

    @classmethod
    def name(cls) -> str:
        return "write"

    @classmethod
    def description(cls) -> str:
        return (
            "Write the full contents of a text file. Parent directories are "
            "created automatically. Existing files are overwritten."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": _WRITE_SCHEMA,
            },
        }

    async def _invoke_async(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(
            await self._os_env.write(
                path=kwargs["path"],
                content=kwargs["content"],
            )
        )


class EditTool(_PiFileTool):
    """``edit`` — exact text replacement, with batch edit support."""

    @classmethod
    def name(cls) -> str:
        return "edit"

    @classmethod
    def description(cls) -> str:
        return (
            "Perform exact text replacements in a file. Supports a single "
            "oldText/newText pair or a batch of edits. Each edit must match "
            "exactly once in the file."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": _EDIT_SCHEMA,
            },
        }

    async def _invoke_async(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(
            await self._os_env.edit(
                path=kwargs["path"],
                old_text=kwargs.get("oldText"),
                new_text=kwargs.get("newText"),
                edits=kwargs.get("edits"),
            )
        )


class BashTool(_PiFileTool):
    """``bash`` — run a shell command."""

    @classmethod
    def name(cls) -> str:
        return "bash"

    @classmethod
    def description(cls) -> str:
        return (
            "Run a shell command and return stdout, stderr, and exit_code. "
            "Use this for commands that require pipelines, environment variables, "
            "or multi-step operations."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": _BASH_SCHEMA,
            },
        }

    async def _invoke_async(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(
            await self._os_env.shell(
                command=kwargs["command"],
                timeout=kwargs.get("timeout"),
            )
        )


class GrepTool(_PiFileTool):
    """``grep`` — search file contents with regex, context, and glob filtering."""

    @classmethod
    def name(cls) -> str:
        return "grep"

    @classmethod
    def description(cls) -> str:
        return (
            "Search file contents for a pattern. Supports regex or literal "
            "matching, case-insensitive mode, glob file filtering, and context "
            "lines around matches. Respects .gitignore. Use this instead of "
            "shell grep/rg for better structured results."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": _GREP_SCHEMA,
            },
        }

    async def _invoke_async(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(
            await self._os_env.grep(
                pattern=kwargs["pattern"],
                path=kwargs.get("path", "."),
                glob=kwargs.get("glob"),
                ignore_case=kwargs.get("ignoreCase", False),
                literal=kwargs.get("literal", False),
                context=kwargs.get("context", 0),
                limit=kwargs.get("limit", 100),
            )
        )


class FindTool(_PiFileTool):
    """``find`` — discover files matching a glob pattern."""

    @classmethod
    def name(cls) -> str:
        return "find"

    @classmethod
    def description(cls) -> str:
        return (
            "Find files and directories matching a glob pattern. Respects "
            ".gitignore and skips vendored directories. Returns paths relative "
            "to the search root. Use this instead of shell find for structured results."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": _FIND_SCHEMA,
            },
        }

    async def _invoke_async(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(
            await self._os_env.find(
                pattern=kwargs["pattern"],
                path=kwargs.get("path", "."),
                limit=kwargs.get("limit", 1000),
            )
        )


class LsTool(_PiFileTool):
    """``ls`` — list directory entries (non-recursive)."""

    @classmethod
    def name(cls) -> str:
        return "ls"

    @classmethod
    def description(cls) -> str:
        return (
            "List directory entries (non-recursive). Includes dotfiles. "
            "Directories are suffixed with '/'. Sorted alphabetically."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": _LS_SCHEMA,
            },
        }

    async def _invoke_async(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(
            await self._os_env.list_dir(
                path=kwargs.get("path", "."),
                limit=kwargs.get("limit", 500),
            )
        )


def build_pi_file_tools(os_env: OSEnvironment) -> list[Tool]:
    """Construct all seven Pi-compatible file tools against *os_env*."""
    return [
        ReadTool(os_env),
        WriteTool(os_env),
        EditTool(os_env),
        BashTool(os_env),
        GrepTool(os_env),
        FindTool(os_env),
        LsTool(os_env),
    ]
