"""Schema-only tool for creating a managed worktree and relocating into it.

This tool is schema-only: execution lives in the runner dispatch
(``_SESSION_CREATE_WORKTREE_TOOLS`` in ``omnigent/runner/tool_dispatch.py``),
which mirrors the web UI's switch-host flow in one shot:

1. ``PATCH /v1/sessions/{id}`` — drop the runner binding (and the
   host-bound model override) so the session is unbound.
2. ``POST /v1/hosts/{host}/runners`` with ``git: {branch_name,
   base_branch, managed: true}`` — the server creates the managed
   (auto-new-worktree) worktree for the target branch, leased to this
   session, and atomically binds + launches the fresh runner inside it.

Works for every harness — relocation replaces the runner process, so
native terminal harnesses move too.
"""

from __future__ import annotations

from typing import Any

from omnigent.tools.base import Tool


class SysSessionCreateWorktreeTool(Tool):
    """Create a managed worktree for a new branch and move the session into it."""

    @classmethod
    def name(cls) -> str:
        return "sys_session_create_worktree"

    @classmethod
    def description(cls) -> str:
        return (
            "Create a new isolated git worktree for the current session and "
            "relocate it there in one step. Branches a target branch off a "
            "parent branch of the source repository on the given host, using "
            "the auto-new-worktree machinery: the worktree is managed and "
            "leased to this session, so the host may reuse a clean managed "
            "folder for it instead of always creating a new one, and reuse "
            "sweeps skip the folder while this session holds it. The session "
            "is then atomically moved into the worktree (fresh runner, chat "
            "history preserved, session shows the new branch). Make this the "
            "last action of the turn — the current turn's connection hands "
            "over to the new runner. The parent folder must be within the "
            "agent's os_env.cwd boundary; uncommitted changes are not "
            "copied; the target branch must not already exist."
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
                                "Host id to create the worktree on and move "
                                "the session to, e.g. 'host_a1b2c3d4...'. May "
                                "be the session's current host. Must be online."
                            ),
                            "minLength": 1,
                        },
                        "parent_folder": {
                            "type": "string",
                            "description": (
                                "Absolute or tilde-prefixed path of the source "
                                "repository on the host, e.g. "
                                "'/Users/me/myrepo' or '~/myrepo'."
                            ),
                            "minLength": 1,
                        },
                        "parent_branch": {
                            "type": "string",
                            "description": (
                                "Base ref to branch from, e.g. 'main' or "
                                "'origin/main'. Omit to branch from the "
                                "repository's current HEAD."
                            ),
                        },
                        "target_branch": {
                            "type": "string",
                            "description": (
                                "New branch to create and check out in the "
                                "worktree, e.g. 'feature/login'. Must not "
                                "already exist."
                            ),
                            "minLength": 1,
                        },
                    },
                    "required": ["host", "parent_folder", "target_branch"],
                    "additionalProperties": False,
                },
            },
        }
