"""Built-in tool: generic PuppyGarden task-API proxy.

A single schema-only tool (``puppygarden_api``) that lets an agent call any
PuppyGarden task REST endpoint on the Omnigent server without shelling out
to ``curl``. The runner dispatches it locally over ``server_client`` — same
posture as the comment / policy / scheduled-task tool families — so auth,
error handling, and JSON in/out are handled centrally and the model emits a
typed function call instead of a raw HTTP command string.

See ``<host.puppygarden.root>/docs/API_REFERENCE.md`` for the full endpoint
catalogue.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from omnigent.tools.base import Tool

# Path prefixes that belong to the PuppyGarden task API surface. The runner
# handler rejects anything outside these so a misbehaving model can't use this
# tool as an arbitrary server proxy.
_TASK_API_PREFIXES: tuple[str, ...] = (
    "/v1/agent-tasks",
    "/v1/agent-queues",
    "/v1/agent-queue-items",
    "/v1/fyi-clusters",
    "/v1/session-watcher",
    "/v1/task-events",
    "/v1/task-items",
    "/v1/task-workers",
    "/v1/worker-providers",
)


def is_task_api_path(path: str) -> bool:
    """Return ``True`` if *path* targets a PuppyGarden task API endpoint.

    :param path: A request path, e.g. ``"/v1/agent-tasks/abc/items"``.
    :returns: ``True`` when the path starts with one of the task-API prefixes.
    """
    decoded = unquote(path)
    if "?" in decoded or "#" in decoded or "\\" in decoded:
        return False
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        return False
    return any(
        decoded == prefix or decoded.startswith(f"{prefix}/") for prefix in _TASK_API_PREFIXES
    )


class PuppyGardenApiTool(Tool):
    """Schema-only tool proxying the PuppyGarden task REST API.

    Execution is runner-dispatched (``_execute_puppygarden_api_tool`` in
    ``omnigent/runner/tool_dispatch.py``); ``invoke`` is never called on the
    AP side.
    """

    @classmethod
    def name(cls) -> str:
        """:returns: ``"puppygarden_api"``."""
        return "puppygarden_api"

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "Call a PuppyGarden task API endpoint on the Omnigent server. "
            "Pass the HTTP method, the API path (starting with /v1/...), "
            "and an optional JSON body / query object. Returns the server's "
            "JSON response. The full endpoint catalogue is under "
            "<host.puppygarden.root>/docs/API_REFERENCE.md. Prefer this over "
            "curl for task work."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format tool schema.

        :returns: A tool schema dict with method / path / body / query args.
        """
        return {
            "type": "function",
            "function": {
                "name": PuppyGardenApiTool.name(),
                "description": PuppyGardenApiTool.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                            "description": "HTTP method for the request.",
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "API path starting with /v1/, e.g. "
                                '"/v1/agent-tasks/<id>/items". Do not include '
                                "the server host — the runner adds it."
                            ),
                        },
                        "body": {
                            "type": "object",
                            "description": (
                                "Optional JSON body for POST/PUT/PATCH requests."
                            ),
                        },
                        "query": {
                            "type": "object",
                            "description": (
                                "Optional query params for GET requests, e.g. "
                                '{"kind": "worker"}.'
                            ),
                        },
                    },
                    "required": ["method", "path"],
                    "additionalProperties": False,
                },
            },
        }
