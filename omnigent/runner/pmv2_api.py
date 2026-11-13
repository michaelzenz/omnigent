"""Send ``pmv2_api`` calls to the server's GlobalHub board API."""

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
    request: dict[str, Any] = {"headers": {ORCHESTRATION_SOURCE_HEADER: source_id}}
    if isinstance(query, dict) and query:
        request["params"] = query
    if isinstance(body, dict) and method in ("POST", "PATCH"):
        request["json"] = body
    try:
        response = await client.request(method, path, timeout=30.0, **request)
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"pmv2_api failed: {exc}"})
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
