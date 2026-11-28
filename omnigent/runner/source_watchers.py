"""Send ``source_watchers`` calls to the source type routes."""

import json
from typing import Any
from urllib.parse import quote

import httpx

from omnigent.runner.pmv2_api import send_board_request
from omnigent.stores.subscription_store import SOURCE_ROUTINGS
from omnigent.tools.builtins.source_watchers import SOURCE_WATCHERS_ACTIONS

_TOOL_NAME = "source_watchers"
_ROUTE = "/v1/agent-tasks/source-types"


async def execute_source_watchers(
    args: dict[str, Any], client: httpx.AsyncClient | None, source_id: str | None
) -> str:
    """
    List the source types, assign one to a watcher or leave it to the fallback watcher,
    or set how one routes its events.

    The server accepts assignments and routings only from the owner's current
    custodian session.

    :param args: Tool arguments: ``action``; ``source_type`` for ``assign``,
        ``unassign`` and ``route``; ``watcher_id`` for ``assign``; ``routing``
        for ``route``.
    :param client: HTTP client pointed at the Omnigent server.
    :param source_id: The calling session.
    :returns: The server's JSON response, or JSON with an ``error``.
    """
    action = args.get("action")
    if action not in SOURCE_WATCHERS_ACTIONS:
        return json.dumps({"error": f"action must be one of {', '.join(SOURCE_WATCHERS_ACTIONS)}"})
    if client is None or source_id is None:
        return json.dumps({"error": f"{_TOOL_NAME} requires an authenticated runner"})
    if action == "list":
        return await send_board_request(client, "GET", _ROUTE, source_id, tool_name=_TOOL_NAME)
    source_type = args.get("source_type")
    if not isinstance(source_type, str) or not source_type.strip():
        return json.dumps({"error": f"{action} needs a source_type"})
    type_route = f"{_ROUTE}/{quote(source_type.strip(), safe='')}"
    if action == "route":
        routing = args.get("routing")
        if routing not in SOURCE_ROUTINGS:
            return json.dumps({"error": f"route needs a routing: {' or '.join(SOURCE_ROUTINGS)}"})
        return await send_board_request(
            client,
            "POST",
            f"{type_route}/routing",
            source_id,
            tool_name=_TOOL_NAME,
            json_body={"routing": routing},
        )
    path = f"{type_route}/watcher"
    if action == "unassign":
        return await send_board_request(client, "DELETE", path, source_id, tool_name=_TOOL_NAME)
    watcher_id = args.get("watcher_id")
    if not isinstance(watcher_id, str) or not watcher_id.strip():
        return json.dumps({"error": "assign needs a watcher_id"})
    return await send_board_request(
        client,
        "POST",
        path,
        source_id,
        tool_name=_TOOL_NAME,
        json_body={"watcher_id": watcher_id.strip()},
    )
