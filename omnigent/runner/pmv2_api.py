"""Send ``pmv2_api`` and ``pmv2_read`` calls to the server's GlobalHub board API."""

import json
from typing import Any

import httpx

from omnigent.runner.identity import ORCHESTRATION_SOURCE_HEADER
from omnigent.tools.builtins.pmv2_api import PMV2_API_METHODS, is_pmv2_api_request

_MAX_RETURNED_TEXT = 500


async def execute_pmv2_api(
    args: dict[str, Any], client: httpx.AsyncClient | None, source_id: str | None
) -> str:
    """
    Send one board API call as the calling session.

    The runner's client acts as the session's owner. The orchestration-source
    header names the calling session; the server can check it against the
    runner's tunnel token.

    :param args: Tool arguments: ``method``, ``path``, and optional ``body`` and
        ``query`` objects.
    :param client: HTTP client pointed at the Omnigent server.
    :param source_id: The calling session.
    :returns: The server's JSON response, or JSON with an ``error``.
    """
    method, path = args.get("method"), args.get("path")
    if method not in PMV2_API_METHODS:
        return json.dumps({"error": f"method must be one of {', '.join(PMV2_API_METHODS)}"})
    if not isinstance(path, str) or not is_pmv2_api_request(method, path):
        return json.dumps(
            {"error": "path must be a GlobalHub board route under /v1/, or GET /v1/hosts"}
        )
    if client is None or source_id is None:
        return json.dumps({"error": "pmv2_api requires an authenticated runner"})
    body, query = args.get("body"), args.get("query")
    return await send_board_request(
        client,
        method,
        path,
        source_id,
        tool_name="pmv2_api",
        json_body=body if isinstance(body, dict) and method in ("POST", "PATCH") else None,
        params=query if isinstance(query, dict) and query else None,
    )


async def execute_pmv2_read(
    args: dict[str, Any], client: httpx.AsyncClient | None, source_id: str | None
) -> str:
    """
    Send one GET request to the board API as the calling session.

    :param args: Tool arguments: ``path`` and an optional ``query`` object.
    :param client: HTTP client pointed at the Omnigent server.
    :param source_id: The calling session.
    :returns: The server's JSON response, or JSON with an ``error``.
    """
    path = args.get("path")
    if not isinstance(path, str) or not is_pmv2_api_request("GET", path):
        return json.dumps(
            {"error": "path must be a GlobalHub board route under /v1/, or /v1/hosts"}
        )
    if client is None or source_id is None:
        return json.dumps({"error": "pmv2_read requires an authenticated runner"})
    query = args.get("query")
    return await send_board_request(
        client,
        "GET",
        path,
        source_id,
        tool_name="pmv2_read",
        params=query if isinstance(query, dict) and query else None,
    )


async def send_board_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    source_id: str,
    *,
    tool_name: str,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """
    Send one board request as the calling session and return the tool output.

    :param client: HTTP client pointed at the Omnigent server.
    :param method: The HTTP method, e.g. ``"POST"``.
    :param path: The server path, e.g. ``"/v1/agent-tasks"``.
    :param source_id: The calling session, named in the orchestration-source header.
    :param tool_name: The tool that a transport error names.
    :param json_body: The JSON body, if any.
    :param params: The query parameters, if any.
    :returns: The server's JSON response, or JSON with an ``error``.
    """
    request: dict[str, Any] = {"headers": {ORCHESTRATION_SOURCE_HEADER: source_id}}
    if params:
        request["params"] = params
    if json_body is not None:
        request["json"] = json_body
    try:
        response = await client.request(method, path, timeout=30.0, **request)
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"{tool_name} failed: {exc}"})
    if response.is_error:
        return json.dumps(
            {
                "error": f"server returned {response.status_code}",
                "details": response.text[:_MAX_RETURNED_TEXT],
            }
        )
    if not response.content:
        return json.dumps({"status": "ok"})
    try:
        return json.dumps(response.json())
    except ValueError:
        return json.dumps({"status": "ok", "body": response.text[:_MAX_RETURNED_TEXT]})
