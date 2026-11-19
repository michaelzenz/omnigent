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
            "Follow outside sources so this session hears when they change, also after this "
            "turn ends. Follow on your own, without being asked, each source that your work "
            "depends on or that can still change: a pull request that you open, review or "
            "wait for; a ticket that you work on; a chat thread that you answer or that asks "
            "you something; a feature flag or its ramp; a deployment, pipeline or job run; an "
            "incident or alert. Before you end a turn, follow each thing that your answer "
            "reports as not finished, e.g. a ramp in progress or a review that waits. When the "
            "user asks you to track, watch or keep an eye on something, subscribe in that "
            "turn, before other work. A source needs no open item and no type in use: anything "
            "with a stable name can be one, e.g. a flag as safe.flag with the flag name as its "
            "key. First call 'types' to see the source types in use, and reuse one that fits, "
            "e.g. github.pull_request; when none fits, name a new type as the system and the "
            "kind of source in lowercase words joined by dots, and a watcher checks it too. "
            "Then call 'subscribe' once for each source with the type, the source's canonical "
            "key (its URL, or a ticket key) and a note of at most 300 words. "
            "When the key is not a web address, also pass url: the https address where a person "
            "opens the source, e.g. the ticket's page, so the user can open it in one click. "
            "Keep the key canonical: events match the key, not the url. "
            "A watcher checks the source on a schedule and reads the note on each run. In the "
            "note, say why this session follows the source and which changes matter. Only when "
            "the source is hard to read, also say how to read it, e.g. the account, the access "
            "or the tool it needs. "
            "Changes come to this session later as [System: event] messages. 'list' shows "
            "what this session follows; 'unsubscribe' stops following a source that you no "
            "longer need. "
            "How to wait, by case: (1) For a change that you expect within about 10 minutes, "
            "wait and check the source again in this turn; a watcher checks only on its "
            "schedule, often every 10 minutes. (2) For any later change, subscribe instead of "
            "a loop: a watcher reports each change, e.g. a comment, a CI result or a merge. "
            "(3) When the work needs a check at a set time even if nothing changes, e.g. a pull "
            "request that is still not merged after 7 days, subscribe and also wake this session "
            "with sys_scheduled_task_create; delete that task when the work finishes."
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
                                "https://github.com/<org>/<repo>/pull/<number>, a ticket key "
                                "or a flag name."
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
                        "url": {
                            "type": "string",
                            "maxLength": 2048,
                            "description": (
                                "For 'subscribe', when source_key is not a web address: the "
                                "http or https address where a person opens the source, e.g. "
                                "the ticket's page. The user sees it as a link."
                            ),
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
