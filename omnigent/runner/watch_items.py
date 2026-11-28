"""Send ``watch_items`` calls to the calling watcher's tracker routes."""

import json
from typing import Any

import httpx

from omnigent.runner.pmv2_api import send_board_request
from omnigent.tools.builtins.watch_items import WATCH_ITEMS_ACTIONS

_ROUTE = "/v1/agent-tasks/watchers/self/items"
_PROBLEM_ROUTE = "/v1/agent-tasks/watchers/self/problem"
_LIST_QUERY_FIELDS = ("status", "source", "limit", "after")


async def execute_watch_items(
    args: dict[str, Any], client: httpx.AsyncClient | None, source_id: str | None
) -> str:
    """
    List, observe or finish the calling watcher's tracked items, or report what it could not read.

    The server finds the watcher from the calling session, which the runner's
    tunnel token confirms.

    :param args: Tool arguments: ``action``; ``items`` for ``observe`` and
        ``done``; ``cursor`` for ``observe``; ``status``, ``source``, ``limit``
        and ``after`` for ``list``; ``sources`` and ``note`` for ``problem``.
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
    if action == "problem":
        body: dict[str, Any] = {"sources": args.get("sources") or []}
        if args.get("note") is not None:
            body["note"] = args["note"]
        return await send_board_request(
            client, "POST", _PROBLEM_ROUTE, source_id, tool_name="watch_items", json_body=body
        )
    items = args.get("items")
    if action == "observe":
        cursor = args.get("cursor")
        if cursor is not None and not isinstance(cursor, str):
            return json.dumps({"error": "cursor must be a string"})
        has_items = isinstance(items, list) and bool(items)
        if not has_items and cursor is None:
            return json.dumps({"error": "observe needs a non-empty items list, a cursor or both"})
        observe_body: dict[str, Any] = {"items": items if has_items else []}
        if cursor is not None:
            observe_body["cursor"] = cursor
        return await send_board_request(
            client,
            "POST",
            f"{_ROUTE}/observe",
            source_id,
            tool_name="watch_items",
            json_body=observe_body,
        )
    if not isinstance(items, list) or not items:
        return json.dumps({"error": f"{action} needs a non-empty items list"})
    # Only an item's key and summary count for 'done'; drop any offsets sent with it.
    items = [_done_item(item) if isinstance(item, dict) else item for item in items]
    return await send_board_request(
        client,
        "POST",
        f"{_ROUTE}/done",
        source_id,
        tool_name="watch_items",
        json_body={"items": items},
    )


def _done_item(item: dict[str, Any]) -> dict[str, Any]:
    done = {"source": item.get("source"), "source_key": item.get("source_key")}
    if item.get("summary") is not None:
        done["summary"] = item["summary"]
    return done
