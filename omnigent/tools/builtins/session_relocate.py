"""Schema-only tool for relocating the current session to another folder.

This tool is schema-only: execution lives in the runner dispatch
(``_SESSION_RELOCATE_TOOLS`` in ``omnigent/runner/tool_dispatch.py``),
which mirrors the web UI's switch-host flow against REST endpoints:

1. ``PATCH /v1/sessions/{id}`` — drop the runner binding (and the
   host-bound model override) so the session is unbound.
2. ``POST /v1/hosts/{host}/runners`` — launch a fresh runner bound to
   the session at the target folder on the target host.

The runner executing the tool is replaced by the new one; chat history
is preserved server-side. Works for every harness because the runner
process is relaunched (unlike ``sys_session_set_workspace``, which can
only move an OmniHarness session's cwd in place).
"""

from __future__ import annotations

from typing import Any

from omnigent.tools.base import Tool


class SysSessionRelocateTool(Tool):
    """Relocate the current session to a folder, optionally on another host."""

    @classmethod
    def name(cls) -> str:
        return "sys_session_relocate"

    @classmethod
    def description(cls) -> str:
        return (
            "Relocate the current session to a different EXISTING folder, "
            "optionally on a different host — another checkout, a worktree "
            "from earlier, any directory already on the target host. To "
            "get a NEW isolated worktree instead, use "
            "sys_session_create_worktree (it creates and moves in one "
            "step). Unbinds the session's current runner and launches a "
            "fresh runner at the target folder; chat history is preserved. "
            "The current turn's connection hands over to the new runner, "
            "so make this the last action of the turn. The folder must be "
            "within the agent's os_env.cwd boundary — except worktree "
            "folders this session already holds a managed claim on, which "
            "are accepted anywhere. Claims lapse after ~24h without use. "
            "Moving away releases the session's claim on its previous "
            "folder, returning it to the host's reuse pool."
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
                        "host": {
                            "type": "string",
                            "description": (
                                "Host id to relocate the session to, e.g. "
                                "'host_a1b2c3d4...'. May be the session's "
                                "current host. The host must be online."
                            ),
                            "minLength": 1,
                        },
                        "folder": {
                            "type": "string",
                            "description": (
                                "Absolute or tilde-prefixed path of the target "
                                "folder on the host, e.g. the worktree_path "
                                "returned by sys_session_create_worktree."
                            ),
                            "minLength": 1,
                        },
                    },
                    "required": ["host", "folder"],
                    "additionalProperties": False,
                },
            },
        }
