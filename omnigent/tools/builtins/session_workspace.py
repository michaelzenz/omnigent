"""Schema-only tool for changing the current session's workspace.

This tool is schema-only: execution lives in the runner dispatch
(``_SESSION_WORKSPACE_TOOLS`` in ``omnigent/runner/tool_dispatch.py``),
which validates the workspace through the server, persists it, and
updates the runner's live workspace cache so subsequent filesystem
tool calls use the new directory.

Only supported for OmniHarness sessions — native terminal harnesses
(Claude Code, Codex, etc.) cannot change their process cwd at runtime.
"""

from __future__ import annotations

from typing import Any

from omnigent.tools.base import Tool


class SysSessionSetWorkspaceTool(Tool):
    """Change the current session's workspace directory."""

    @classmethod
    def name(cls) -> str:
        return "sys_session_set_workspace"

    @classmethod
    def description(cls) -> str:
        return (
            "Change the current session's workspace to an existing directory on the "
            "session's host. The directory must already exist and must be within the "
            "agent's os_env.cwd boundary. Subsequent filesystem tool calls "
            "(sys_os_shell, sys_os_read, sys_os_write, etc.) and newly launched "
            "terminals will use the new workspace. Already-running commands and "
            "terminals remain in their original cwd. Only supported for OmniHarness "
            "sessions."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "description": (
                                "Absolute or tilde-prefixed path to an existing "
                                "directory on the session's host, e.g. "
                                "'/Users/me/projects/myapp' or '~/projects/myapp'."
                            ),
                            "minLength": 1,
                        },
                    },
                    "required": ["workspace"],
                    "additionalProperties": False,
                },
            },
        }
