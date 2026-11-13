"""Schema-only tool for calling the GlobalHub board API (``pmv2_api``)."""

from typing import Any
from urllib.parse import unquote

from omnigent.tools.base import Tool

PMV2_API_METHODS = ("GET", "POST", "PATCH", "DELETE")
# Any method may reach the board's own routes.
_BOARD_PATH_PREFIXES = (
    "/v1/agent-tasks",
    "/v1/task-events",
    "/v1/task-workers",
    "/v1/fyi-clusters",
)
# Outside the board, only reads that a role manual makes.
_READ_ONLY_PATHS = frozenset({"/v1/hosts"})


def is_pmv2_api_request(method: str, path: str) -> bool:
    """
    Whether ``pmv2_api`` may send ``method`` to ``path``.

    :param method: An HTTP method, e.g. ``"PATCH"``.
    :param path: A server path, e.g. ``"/v1/agent-tasks/managers/self"``.
    :returns: ``True`` for the board's routes and for ``GET /v1/hosts``.
    """
    decoded = unquote(path)
    if any(char in decoded for char in "?#\\"):
        return False
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        return False
    if method == "GET" and decoded in _READ_ONLY_PATHS:
        return True
    return any(
        decoded == prefix or decoded.startswith(f"{prefix}/") for prefix in _BOARD_PATH_PREFIXES
    )


class Pmv2ApiTool(Tool):
    """Opt-in: registered only for a spec that declares it under ``tools.builtins``."""

    @classmethod
    def name(cls) -> str:
        return "pmv2_api"

    @classmethod
    def description(cls) -> str:
        return (
            "Call the user's GlobalHub task board API on the Omnigent server. Pass the HTTP "
            "method, the API path starting with /v1/, and an optional JSON body or query "
            "object; the result is the server's JSON response. Only the board's "
            "/v1/agent-tasks, /v1/task-events, /v1/task-workers and /v1/fyi-clusters routes "
            "and GET /v1/hosts are allowed."
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
                        "method": {
                            "type": "string",
                            "enum": list(PMV2_API_METHODS),
                            "description": "HTTP method for the request.",
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                'API path starting with /v1/, e.g. "/v1/agent-tasks/<id>". '
                                "Do not include the server host or a query string."
                            ),
                        },
                        "body": {
                            "type": "object",
                            "description": "Optional JSON body for POST and PATCH requests.",
                        },
                        "query": {
                            "type": "object",
                            "description": (
                                'Optional query parameters, e.g. {"state": "active"}.'
                            ),
                        },
                    },
                    "required": ["method", "path"],
                    "additionalProperties": False,
                },
            },
        }
