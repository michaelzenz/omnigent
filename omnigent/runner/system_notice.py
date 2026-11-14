"""Send ``system_notice`` calls to the server's system notice routes."""

import json
from typing import Any

import httpx

from omnigent.runner.pmv2_api import send_board_request
from omnigent.tools.builtins.system_notice import SYSTEM_NOTICE_ACTIONS

_ROUTE = "/v1/agent-tasks/system-notice"
_PUBLISH_FIELDS = ("title", "body", "body_kind", "actions")


async def execute_system_notice(
    args: dict[str, Any], client: httpx.AsyncClient | None, source_id: str | None
) -> str:
    """
    Publish, read or clear the system notice of the calling session's owner.

    The server names the calling session as a notice's publisher when the
    runner's tunnel token confirms it.

    :param args: Tool arguments: ``action``, and for ``publish`` the notice's
        ``title``, ``body``, ``body_kind`` and ``actions``.
    :param client: HTTP client pointed at the Omnigent server.
    :param source_id: The calling session.
    :returns: The server's JSON response, or JSON with an ``error``.
    """
    action = args.get("action")
    if action not in SYSTEM_NOTICE_ACTIONS:
        return json.dumps({"error": f"action must be one of {', '.join(SYSTEM_NOTICE_ACTIONS)}"})
    if client is None or source_id is None:
        return json.dumps({"error": "system_notice requires an authenticated runner"})
    if action == "get":
        return await send_board_request(
            client, "GET", _ROUTE, source_id, tool_name="system_notice"
        )
    if action == "clear":
        return await send_board_request(
            client, "POST", f"{_ROUTE}/dismiss", source_id, tool_name="system_notice"
        )
    notice = {field: args[field] for field in _PUBLISH_FIELDS if args.get(field) is not None}
    return await send_board_request(
        client, "POST", _ROUTE, source_id, tool_name="system_notice", json_body=notice
    )
