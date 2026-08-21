"""Schema-only tools for server-mediated skill updates and creation."""

from __future__ import annotations

from typing import Any

from omnigent.tools.base import Tool, ToolContext


class UpdateSkillTool(Tool):
    """Expose the schema for updating one resolved skill variant."""

    @classmethod
    def name(cls) -> str:
        return "update_skill"

    @classmethod
    def description(cls) -> str:
        return (
            "Update text files in an existing skill. The effective variant for this "
            "session is selected automatically and every matching host copy is updated."
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
                        "name": {
                            "type": "string",
                            "description": "The existing skill name.",
                        },
                        "files": {
                            "type": "object",
                            "description": (
                                "Relative text file paths mapped to their complete new contents. "
                                "Only existing files are updated."
                            ),
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["name", "files"],
                    "additionalProperties": False,
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        raise RuntimeError("update_skill must be dispatched by the runner")


class WriteSkillTool(Tool):
    """Expose the schema for creating a skill on detected harness roots."""

    @classmethod
    def name(cls) -> str:
        return "write_skill"

    @classmethod
    def description(cls) -> str:
        return (
            "Create a new skill on every detected, enabled harness home. "
            "Use update_skill when the skill already exists."
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
                        "name": {
                            "type": "string",
                            "description": "The new skill name and directory name.",
                        },
                        "files": {
                            "type": "object",
                            "description": (
                                "Relative text file paths mapped to contents. "
                                "SKILL.md is required and its frontmatter name must match."
                            ),
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["name", "files"],
                    "additionalProperties": False,
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        raise RuntimeError("write_skill must be dispatched by the runner")
