"""Schema-only tools for project management and session project membership.

These tools are schema-only: execution lives in the runner dispatch
(``_PROJECT_TOOLS`` / ``_SESSION_PROJECT_TOOLS`` in
``omnigent/runner/tool_dispatch.py``), which proxies the existing
``POST /v1/projects``, ``GET /v1/projects``, and
``PATCH /v1/sessions/{id}`` REST endpoints via ``server_client``.
"""

from __future__ import annotations

from typing import Any

from omnigent.tools.base import Tool


class SysProjectCreateTool(Tool):
    """Create a new, empty project owned by the calling user."""

    @classmethod
    def name(cls) -> str:
        return "sys_project_create"

    @classmethod
    def description(cls) -> str:
        return (
            "Create a new, empty project owned by the calling user. Projects are "
            "owner-private containers that group sessions. Creating a project does "
            "not move the current session into it — use sys_session_set_project for "
            "that. Returns the project id, name, and timestamps."
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
                            "description": ("Human-readable project name, e.g. 'Managed Tables'."),
                            "minLength": 1,
                            "maxLength": 200,
                        },
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        }


class SysProjectListTool(Tool):
    """List the calling user's projects."""

    @classmethod
    def name(cls) -> str:
        return "sys_project_list"

    @classmethod
    def description(cls) -> str:
        return (
            "List the calling user's projects. Returns projects with their id, name, "
            "and timestamps. Use the returned id with sys_session_set_project to file "
            "the current session into a project."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        }


class SysSessionSetProjectTool(Tool):
    """File (or unfile) the current session into a project."""

    @classmethod
    def name(cls) -> str:
        return "sys_session_set_project"

    @classmethod
    def description(cls) -> str:
        return (
            "Move the current session into a project, or unfile it. Pass a project id "
            "to file the session into that project (must be one you own). Pass null to "
            "unfile the session from its current project. The project must already "
            "exist — use sys_project_create to create one. This only affects the "
            "current session."
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
                        "project_id": {
                            "type": ["string", "null"],
                            "description": (
                                "The project id to file the session into, or null to "
                                "unfile it from its current project."
                            ),
                        },
                    },
                    "required": ["project_id"],
                    "additionalProperties": False,
                },
            },
        }
