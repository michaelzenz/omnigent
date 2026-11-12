"""Schema-only tool for permanently deleting one of the owner's sessions."""

from typing import Any

from omnigent.tools.base import Tool


class SysSessionDeleteTool(Tool):
    """Opt-in: registered only for a spec that declares it under ``tools.builtins``."""

    @classmethod
    def name(cls) -> str:
        return "sys_session_delete"

    @classmethod
    def description(cls) -> str:
        return (
            "Permanently delete one of the user's sessions: its tasks, runner resources, "
            "files and history. This cannot be undone, so only delete a session the user "
            "approved deleting; to hide a session instead, archive it with "
            "sys_session_update. Find sessions with sys_session_list or sys_project_list. "
            "A session cannot delete itself or a session that shares its runner."
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
                        "session_id": {
                            "type": "string",
                            "description": "The session to delete.",
                        },
                        "delete_branch": {
                            "type": "boolean",
                            "description": (
                                "Also remove the session's server-created worktree and its "
                                "git branch. Defaults to false, which leaves them in place."
                            ),
                        },
                    },
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
            },
        }
