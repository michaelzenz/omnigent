"""Send ``watch_items`` calls to the calling watcher's tracker routes."""

import json
from typing import Any

import httpx

from omnigent.runner.pmv2_api import send_board_request
from omnigent.tools.builtins.watch_items import WATCH_ITEMS_ACTIONS

_ROUTE = "/v1/agent-tasks/watchers/self/items"
_LIST_QUERY_FIELDS = ("status", "source", "limit", "after")


async def execute_watch_items(
    args: dict[str, Any], client: httpx.AsyncClient | None, source_id: str | None
) -> str:
    """
    List, observe or finish the calling watcher's tracked items.

    The server finds the watcher from the calling session, which the runner's
    tunnel token confirms.

    :param args: Tool arguments: ``action``; ``items`` for ``observe`` and
        ``done``; ``status``, ``source``, ``limit`` and ``after`` for ``list``.
    :param client: HTTP client pointed at the Omnigent server.
    :param source_id: The calling session.
    :returns: The server's JSON response, or JSON with an ``error``.
    """
    action = args.get("action")
    if action not in WATCH_ITEMS_ACTIONS:
        return json.dumps({"error": f"action must be one of {', '.join(WATCH_ITEMS_ACTIONS)}"})
    if client is None or source_id is None:
        return json.dumps({"error": "watch_items requires an authenticated runner"})
    if action == "list":
        query = {field: args[field] for field in _LIST_QUERY_FIELDS if args.get(field) is not None}
        return await send_board_request(
            client, "GET", _ROUTE, source_id, tool_name="watch_items", params=query or None
        )
    items = args.get("items")
    if not isinstance(items, list) or not items:
        return json.dumps({"error": f"{action} needs a non-empty items list"})
    if action == "observe":
        path = f"{_ROUTE}/observe"
    else:
        path = f"{_ROUTE}/done"
        # Only an item's key and summary count for 'done'; drop any offsets sent with it.
        items = [_done_item(item) if isinstance(item, dict) else item for item in items]
    return await send_board_request(
        client, "POST", path, source_id, tool_name="watch_items", json_body={"items": items}
    )


def _done_item(item: dict[str, Any]) -> dict[str, Any]:
    done = {"source": item.get("source"), "source_key": item.get("source_key")}
    if item.get("summary") is not None:
        done["summary"] = item["summary"]
    return done
