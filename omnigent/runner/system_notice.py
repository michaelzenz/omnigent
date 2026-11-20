"""Send ``system_notice`` calls to the server's system notice routes."""

import json
import re
from typing import Any

import httpx

from omnigent.runner.pmv2_api import send_board_request
from omnigent.tools.builtins.system_notice import SYSTEM_NOTICE_ACTIONS

_ROUTE = "/v1/agent-tasks/system-notice"
_DRAFT_ROUTE = f"{_ROUTE}/draft"
_PUBLISH_FIELDS = ("title", "body", "body_kind", "actions")
_CHANGE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


async def execute_system_notice(
    args: dict[str, Any], client: httpx.AsyncClient | None, source_id: str | None
) -> str:
    """
    Publish, read or clear the system notice of the calling session's owner, or
    stage changes for the next notice and commit them as one.

    The server names the calling session as a notice's publisher when the
    runner's tunnel token confirms it.

    :param args: Tool arguments: ``action``; for ``publish`` the notice's
        ``title``, ``body``, ``body_kind`` and ``actions``; for ``stage`` a
        ``change``; for ``unstage`` a ``change_id``; for ``commit`` an
        optional ``title``.
    :param client: HTTP client pointed at the Omnigent server.
    :param source_id: The calling session.
    :returns: The server's JSON response, or JSON with an ``error``.
    """
    action = args.get("action")
    if action not in SYSTEM_NOTICE_ACTIONS:
        return json.dumps({"error": f"action must be one of {', '.join(SYSTEM_NOTICE_ACTIONS)}"})
    if client is None or source_id is None:
        return json.dumps({"error": "system_notice requires an authenticated runner"})

    async def send(method: str, path: str, body: dict[str, Any] | None = None) -> str:
        return await send_board_request(
            client, method, path, source_id, tool_name="system_notice", json_body=body
        )

    if action == "get":
        return await send("GET", _ROUTE)
    if action == "clear":
        return await send("POST", f"{_ROUTE}/dismiss")
    if action == "stage":
        change = args.get("change")
        if not isinstance(change, dict):
            return json.dumps({"error": "stage requires a change"})
        return await send("POST", f"{_DRAFT_ROUTE}/changes", {"change": change})
    if action == "unstage":
        change_id = args.get("change_id")
        if not isinstance(change_id, str) or not _CHANGE_ID.fullmatch(change_id):
            return json.dumps({"error": "unstage requires the change_id of a staged change"})
        return await send("DELETE", f"{_DRAFT_ROUTE}/changes/{change_id}")
    if action == "check":
        return await send("GET", _DRAFT_ROUTE)
    if action == "commit":
        return await send(
            "POST", f"{_DRAFT_ROUTE}/commit", {"title": args["title"]} if args.get("title") else {}
        )
    if action == "discard":
        return await send("DELETE", _DRAFT_ROUTE)
    notice = {field: args[field] for field in _PUBLISH_FIELDS if args.get(field) is not None}
    return await send("POST", _ROUTE, notice)
