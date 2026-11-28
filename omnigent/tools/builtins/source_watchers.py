"""Schema-only tool for the custodian's assignments of source types (``source_watchers``)."""

from typing import Any

from omnigent.stores.subscription_store import SOURCE_ROUTINGS
from omnigent.tools.base import Tool

SOURCE_WATCHERS_ACTIONS = ("list", "assign", "unassign", "route")


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
            "watcher whose manual covers it, and set how its events route. 'list' shows each "
            "source type in use with its watcher and its routing; 'assign' gives a type to a "
            "watcher, in place of the one it had; 'unassign' leaves a type to the built-in "
            "fallback watcher; 'route' sets the routing of a type: 'broker' sends each event "
            "only to the broker, for a stream in which one source carries many topics, e.g. a "
            "direct message, a group chat, a channel or an inbox; 'followers' sends each event "
            "to the sessions that follow its source, for one piece of work, e.g. a pull request, "
            "a ticket, a document or a thread."
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
                                "watcher, 'unassign' leaves one to the fallback watcher, and "
                                "'route' sets the routing of one."
                            ),
                        },
                        "source_type": {
                            "type": "string",
                            "maxLength": 64,
                            "description": (
                                "For 'assign', 'unassign' and 'route': the source type."
                            ),
                        },
                        "watcher_id": {
                            "type": "string",
                            "maxLength": 64,
                            "description": "For 'assign': the id of the watcher.",
                        },
                        "routing": {
                            "type": "string",
                            "enum": list(SOURCE_ROUTINGS),
                            "description": "For 'route': 'broker' or 'followers'.",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
