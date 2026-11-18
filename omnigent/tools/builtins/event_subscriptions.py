"""Schema-only tool that makes a session follow outside sources (``event_subscriptions``)."""

from typing import Any

from omnigent.tools.base import Tool

EVENT_SUBSCRIPTIONS_ACTIONS = ("types", "subscribe", "unsubscribe", "list")


class EventSubscriptionsTool(Tool):
    """Framework-owned: every session can follow sources; the runner calls the session API."""

    @classmethod
    def name(cls) -> str:
        """Return the tool name."""
        return "event_subscriptions"

    @classmethod
    def description(cls) -> str:
        """Return the agent-facing contract."""
        return (
            "Follow outside sources, such as a pull request, a ticket or a chat thread, so "
            "this session hears when they change. ALWAYS follow a source when your work "
            "depends on it: right after you open a pull request, when you start work on a "
            "ticket, or when the user asks you to keep an eye on something. First call 'types' "
            "to see "
            "the source types in use, and reuse one that fits, e.g. github.pull_request; "
            "only when none fits, name a new type as the system and the kind of source in "
            "lowercase words joined by dots. Then call 'subscribe' with the type, the "
            "source's canonical key (its URL, or a ticket key) and a note of at most 300 words. "
            "A watcher checks the source on a schedule and reads the note on each run. In the "
            "note, say why this session follows the source. Only when the source is hard to "
            "read, also say how to read it, e.g. the account, the access or the tool it needs. "
            "Changes come to this session later as [System: event] messages. 'list' shows "
            "what this session follows; 'unsubscribe' stops following a source that you no "
            "longer need. "
            "Use this instead of a loop that waits and checks the source again: a watcher "
            "checks it for you, also after this turn ends. A watcher checks only on its "
            "schedule, often every 30 minutes; for a wait of a few minutes, check in this turn."
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
                            "enum": list(EVENT_SUBSCRIPTIONS_ACTIONS),
                            "description": (
                                "'types' lists the source types in use, 'subscribe' follows "
                                "a source, 'unsubscribe' stops following one, and 'list' "
                                "shows the sources this session follows."
                            ),
                        },
                        "source_type": {
                            "type": "string",
                            "maxLength": 64,
                            "description": (
                                "For 'subscribe': the source type, e.g. "
                                "github.pull_request or jira.issue."
                            ),
                        },
                        "source_key": {
                            "type": "string",
                            "maxLength": 512,
                            "description": (
                                "For 'subscribe' and 'unsubscribe': the source in its "
                                "canonical form, e.g. "
                                "https://github.com/<org>/<repo>/pull/<number> or a ticket key."
                            ),
                        },
                        "note": {
                            "type": "string",
                            "maxLength": 3000,
                            "description": (
                                "For 'subscribe': at most 300 words on why this session "
                                "follows the source. Only when the source is hard to read, "
                                'also how a watcher reads it, e.g. "Private repository: read '
                                'it with the GitHub MCP server of the work account."'
                            ),
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
