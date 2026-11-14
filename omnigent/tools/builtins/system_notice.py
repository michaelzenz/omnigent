"""Schema-only tool for the system notice pinned on the GlobalHub board (``system_notice``)."""

from typing import Any

from omnigent.tools.base import Tool

SYSTEM_NOTICE_ACTIONS = ("publish", "get", "clear")

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "The key whose value the click carries."},
        "kind": {"type": "string", "enum": ["toggle", "text", "textarea"]},
        "label": {"type": "string"},
        "group": {
            "type": "string",
            "description": (
                "Optional label shared by related inputs, e.g. a host; the user can switch "
                "a whole group on or off at once."
            ),
        },
        "placeholder": {"type": "string", "description": "Hint shown in an empty text field."},
        "default": {"type": "boolean", "description": "A toggle's initial value."},
        "required": {"type": "boolean"},
    },
    "required": ["id", "kind"],
    "additionalProperties": False,
}

_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "The action id a click carries."},
        "label": {"type": "string", "description": "The button text."},
        "inputs": {"type": "array", "items": _INPUT_SCHEMA},
    },
    "required": ["id"],
    "additionalProperties": False,
}


class SystemNoticeTool(Tool):
    """Opt-in: registered only for a spec that declares it under ``tools.builtins``."""

    @classmethod
    def name(cls) -> str:
        return "system_notice"

    @classmethod
    def description(cls) -> str:
        return (
            "Publish, read or clear the system notice pinned on the user's GlobalHub board. "
            "The user has at most one active notice: publishing replaces it, and it stays "
            "until the user dismisses it or you clear it. A notice has a title, an optional "
            "markdown or HTML body, and declared actions: buttons the user can click, each "
            "with optional inputs (toggles and text fields) whose values the click carries."
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
                            "enum": list(SYSTEM_NOTICE_ACTIONS),
                            "description": (
                                "'publish' replaces the active notice, 'get' reads it, and "
                                "'clear' dismisses it."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": "The notice's headline (publish only).",
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "Optional body (publish only). Markdown by default. With "
                                "body_kind 'html' it is a page shown in a sandboxed frame: "
                                'bind buttons with data-action="<action id>" and controls '
                                'with data-notice-input="<input id>".'
                            ),
                        },
                        "body_kind": {
                            "type": "string",
                            "enum": ["markdown", "html"],
                            "description": "How the board shows the body (publish only).",
                        },
                        "actions": {
                            "type": "array",
                            "items": _ACTION_SCHEMA,
                            "description": (
                                "The buttons the user can click (publish only). Action ids "
                                "are unique, and input ids are unique within an action."
                            ),
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
