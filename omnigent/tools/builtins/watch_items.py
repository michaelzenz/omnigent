"""Schema-only tool for a GlobalHub watcher's tracker (``watch_items``)."""

from typing import Any

from omnigent.entities import MAX_WATCHER_CURSOR_BYTES, MAX_WATCHER_PROBLEM_NOTE_WORDS
from omnigent.tools.base import Tool

WATCH_ITEMS_ACTIONS = ("list", "observe", "done", "problem")

_PROBLEM_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "description": 'The source type, e.g. "slack.conversation".',
        },
        "source_key": {
            "type": "string",
            "description": (
                "The item that you could not read, e.g. a conversation link; leave it out "
                "when you could read no item of this source type."
            ),
        },
        "reason": {
            "type": "string",
            "description": (
                'What fails, in a few words, e.g. "the privacy filter hides the message '
                'text" or "no access to the Slack MCP server".'
            ),
        },
    },
    "required": ["source", "reason"],
    "additionalProperties": False,
}

_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "description": (
                "The source type: the system and the kind of item, in lowercase words joined "
                'by dots, e.g. "github.pull_request" or "jira.issue". For a source that '
                "sessions follow, use the type of their subscription."
            ),
        },
        "source_key": {
            "type": "string",
            "description": (
                "What you track there, in its canonical form: a pull request as "
                "https://github.com/<org>/<repo>/pull/<number> (no query, fragment, trailing "
                "slash or page such as /files), a ticket as its key. Sessions follow keys by "
                "an exact match, so always write a key the same way."
            ),
        },
        "offsets": {
            "type": "object",
            "additionalProperties": {"type": ["string", "null"]},
            "description": (
                "The values to compare with your last run, by name, e.g. "
                '{"state": "open", "checks": "failure@abc123", "comments": "987"}. Use values '
                "that change only when something important happens. Null removes a name; "
                "names you leave out keep their value."
            ),
        },
        "title": {
            "type": "string",
            "description": (
                'A short label, e.g. "repo#12 Fix retry"; omit it to keep the saved one.'
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "One line that explains a change and who made it, e.g. "
                '"CI failed: unit-tests" or "alice commented: please add a test". For '
                "'done', why the item is done, e.g. \"Merged\"."
            ),
        },
        "details": {
            "type": "object",
            "description": "More about a change, e.g. the failing checks or a new comment's text.",
        },
        "announce": {
            "type": "boolean",
            "description": (
                "Report a new item as news, e.g. a new review request. Without it, the first "
                "observation of an item only saves its offsets."
            ),
        },
    },
    "required": ["source", "source_key"],
    "additionalProperties": False,
}


class WatchItemsTool(Tool):
    """Opt-in: registered only for a spec that declares it under ``tools.builtins``."""

    @classmethod
    def name(cls) -> str:
        return "watch_items"

    @classmethod
    def description(cls) -> str:
        return (
            "Track the things you watch across your runs; you can track any number of items. "
            "'observe' sends each item's current state as named offsets; the server compares "
            "them with the last run and reports each change to the sessions that follow the "
            "item's source_key, else to the broker, so do not report changes any other way. "
            "A new item is a silent first look unless you set announce. 'observe' can also "
            f"save a cursor of at most {MAX_WATCHER_CURSOR_BYTES} bytes for your next run, e.g. "
            "the time of the newest message that you read; your next run message shows it "
            "under [Cursor]. 'list' returns a page "
            "of your items with the offsets you saw last, in the order you started to track "
            "them; while has_more is true, pass the page's last_id as after for the next "
            "page. 'done' marks items finished, e.g. merged pull requests, and tells the "
            "sessions that follow them; an observation that changes a done item's offsets "
            "reopens it. 'problem' reports the sources that you could not read in this run; "
            "the board shows them to the user until a run ends without a report."
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
                        "action": {
                            "type": "string",
                            "enum": list(WATCH_ITEMS_ACTIONS),
                            "description": (
                                "'list' reads your items, 'observe' reports what you see "
                                "now, 'done' marks items finished, and 'problem' reports "
                                "the sources that you could not read."
                            ),
                        },
                        "sources": {
                            "type": "array",
                            "description": (
                                "For 'problem': each source that you could not read in this "
                                "run, with what fails. Each call replaces your report, so "
                                "send all of them in one call; an empty list clears it."
                            ),
                            "items": _PROBLEM_SOURCE_SCHEMA,
                        },
                        "note": {
                            "type": "string",
                            "description": (
                                f"For 'problem': at most {MAX_WATCHER_PROBLEM_NOTE_WORDS} "
                                "words for the user, only when it helps, e.g. how to fix it."
                            ),
                        },
                        "items": {
                            "type": "array",
                            "description": (
                                "For 'observe', up to 100 items, each with its offsets; leave "
                                "it out to save only a cursor. For 'done', up to 100 items; "
                                "only source, source_key and summary count."
                            ),
                            "items": _ITEM_SCHEMA,
                        },
                        "cursor": {
                            "type": "string",
                            "description": (
                                f"For 'observe': at most {MAX_WATCHER_CURSOR_BYTES} bytes "
                                "that your next run message shows under [Cursor], e.g. the "
                                "time of the newest message that you read; an empty string "
                                "clears it. Send it with the items that it covers, or alone."
                            ),
                        },
                        "status": {
                            "type": "string",
                            "enum": ["active", "done", "all"],
                            "description": "For 'list': which items; active by default.",
                        },
                        "source": {
                            "type": "string",
                            "description": "For 'list': only this system's items.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "For 'list': the page size; 100 by default, up to 500.",
                        },
                        "after": {
                            "type": "string",
                            "description": "For 'list': the last_id of the previous page.",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
