"""Send ``event_subscriptions`` calls to the session subscription routes."""

import json
from typing import Any

import httpx

from omnigent.runner.pmv2_api import send_board_request
from omnigent.tools.builtins.event_subscriptions import EVENT_SUBSCRIPTIONS_ACTIONS

_TOOL_NAME = "event_subscriptions"
_SOURCE_TYPES_ROUTE = "/v1/agent-tasks/source-types"


async def execute_event_subscriptions(
    args: dict[str, Any], client: httpx.AsyncClient | None, source_id: str | None
) -> str:
    """
    List the source types, or follow, stop following or list the calling session's sources.

    :param args: Tool arguments: ``action``; ``source_type``, ``source_key`` and
        ``note`` for ``subscribe``; ``source_key`` for ``unsubscribe``.
    :param client: HTTP client pointed at the Omnigent server.
    :param source_id: The calling session.
    :returns: The server's JSON response, or JSON with an ``error``.
    """
    action = args.get("action")
    if action not in EVENT_SUBSCRIPTIONS_ACTIONS:
        return json.dumps(
            {"error": f"action must be one of {', '.join(EVENT_SUBSCRIPTIONS_ACTIONS)}"}
        )
    if client is None or source_id is None:
        return json.dumps({"error": f"{_TOOL_NAME} requires an authenticated runner"})
    route = f"/v1/sessions/{source_id}/subscriptions"
    if action == "types":
        return await send_board_request(
            client, "GET", _SOURCE_TYPES_ROUTE, source_id, tool_name=_TOOL_NAME
        )
    if action == "list":
        return await send_board_request(client, "GET", route, source_id, tool_name=_TOOL_NAME)
    source_key = args.get("source_key")
    if not isinstance(source_key, str) or not source_key.strip():
        return json.dumps({"error": f"{action} needs a source_key"})
    if action == "unsubscribe":
        return await send_board_request(
            client,
            "POST",
            f"{route}/remove",
            source_id,
            tool_name=_TOOL_NAME,
            json_body={"source_key": source_key},
        )
    source_type = args.get("source_type")
    if not isinstance(source_type, str) or not source_type.strip():
        return json.dumps({"error": "subscribe needs a source_type; call 'types' first"})
    body: dict[str, Any] = {"source_type": source_type, "source_key": source_key}
    if isinstance(args.get("note"), str):
        body["note"] = args["note"]
    return await send_board_request(
        client, "POST", route, source_id, tool_name=_TOOL_NAME, json_body=body
    )
