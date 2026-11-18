"""Schema-only tool for the custodian's assignments of source types (``source_watchers``)."""

from typing import Any

from omnigent.tools.base import Tool

SOURCE_WATCHERS_ACTIONS = ("list", "assign", "unassign")


class SourceWatchersTool(Tool):
    """Opt-in: registered only for a spec that declares it under ``tools.builtins``."""

    @classmethod
    def name(cls) -> str:
        """Return the tool name."""
        return "source_watchers"

    @classmethod
    def description(cls) -> str:
        """Return the agent-facing contract."""
        return (
            "Give each source type that sessions follow, e.g. github.pull_request, to the "
            "watcher whose manual covers it. 'list' shows each source type in use with its "
            "watcher; 'assign' gives a type to a watcher, in place of the one it had; "
            "'unassign' leaves a type to the built-in fallback watcher."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": list(SOURCE_WATCHERS_ACTIONS),
                            "description": (
                                "'list' reads the source types, 'assign' gives one to a "
                                "watcher, and 'unassign' leaves one to the fallback watcher."
                            ),
                        },
                        "source_type": {
                            "type": "string",
                            "maxLength": 64,
                            "description": "For 'assign' and 'unassign': the source type.",
                        },
                        "watcher_id": {
                            "type": "string",
                            "maxLength": 64,
                            "description": "For 'assign': the id of the watcher.",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
