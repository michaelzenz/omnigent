"""Lazy MCP tool discovery for harnesses without native tool search."""

from __future__ import annotations

import json
import re
from typing import Any

from omnigent.json_types import JsonObject as _JsonObject

MCP_TOOL_SEARCH_NAME = "mcp_tool_search"
MCP_TOOL_CALL_NAME = "mcp_tool_call"

_DEFAULT_RESULT_LIMIT = 5
_MAX_RESULT_LIMIT = 10
_TERM_RE = re.compile(r"[a-z0-9]+")


def is_namespaced_mcp_tool(schema: dict[str, Any]) -> bool:
    """Return whether *schema* uses Omnigent's ``server__tool`` MCP name."""
    name = _schema_name(schema)
    return name is not None and "__" in name


def openai_agents_lazy_tool_schemas(
    schemas: list[_JsonObject],
) -> list[_JsonObject]:
    """Replace namespaced MCP schemas with search and generic-call tools."""
    has_mcp = any(is_namespaced_mcp_tool(schema) for schema in schemas)
    if not has_mcp:
        return list(schemas)
    eager = [
        schema
        for schema in schemas
        if not is_namespaced_mcp_tool(schema)
        and _schema_name(schema) not in {MCP_TOOL_SEARCH_NAME, MCP_TOOL_CALL_NAME}
    ]
    return [*eager, _search_tool_schema(), _call_tool_schema()]


def search_mcp_tool_schemas(
    schemas: list[_JsonObject],
    *,
    query: str,
    server: str | None = None,
    limit: int = _DEFAULT_RESULT_LIMIT,
) -> _JsonObject:
    """Return the best matching namespaced MCP schemas."""
    normalized_query = query.strip().casefold()
    normalized_server = server.strip().casefold() if isinstance(server, str) else ""
    safe_limit = max(1, min(limit, _MAX_RESULT_LIMIT))
    terms = _TERM_RE.findall(normalized_query)

    ranked: list[tuple[int, str, _JsonObject]] = []
    available_servers: set[str] = set()
    for schema in schemas:
        name = _schema_name(schema)
        if name is None or "__" not in name:
            continue
        server_name, _, bare_name = name.partition("__")
        available_servers.add(server_name)
        if normalized_server and server_name.casefold() != normalized_server:
            continue

        description = _schema_description(schema)
        parameters = _schema_parameters(schema)
        score = _search_score(
            query=normalized_query,
            terms=terms,
            server_name=server_name,
            bare_name=bare_name,
            description=description,
            parameters=parameters,
        )
        if normalized_query and score <= 0:
            continue
        ranked.append((score, name, schema))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    matches = [
        {
            "name": name,
            "description": _schema_description(schema),
            "parameters": _schema_parameters(schema),
        }
        for _, name, schema in ranked[:safe_limit]
    ]
    return {
        "query": query,
        "server": server,
        "tools": matches,
        "available_servers": sorted(available_servers),
    }


def _search_score(
    *,
    query: str,
    terms: list[str],
    server_name: str,
    bare_name: str,
    description: str,
    parameters: _JsonObject,
) -> int:
    server_text = " ".join(_TERM_RE.findall(server_name.casefold()))
    bare_name_text = " ".join(_TERM_RE.findall(bare_name.casefold()))
    name_text = f"{server_text} {bare_name_text}"
    description_text = description.casefold()
    parameter_text = json.dumps(parameters, sort_keys=True).casefold()
    score = 0
    if query and query in name_text:
        score += 100
    for term in terms:
        if term in server_text:
            score += 50
        if term in bare_name_text:
            score += 25
        if term in description_text:
            score += 5
        if term in parameter_text:
            score += 1
    return score


def _schema_name(schema: dict[str, Any]) -> str | None:
    name = schema.get("name")
    if isinstance(name, str) and name:
        return name
    function = schema.get("function")
    if isinstance(function, dict):
        nested = function.get("name")
        if isinstance(nested, str) and nested:
            return nested
    return None


def _schema_description(schema: dict[str, Any]) -> str:
    description = schema.get("description")
    if isinstance(description, str):
        return description
    function = schema.get("function")
    if isinstance(function, dict):
        nested = function.get("description")
        if isinstance(nested, str):
            return nested
    return ""


def _schema_parameters(schema: dict[str, Any]) -> _JsonObject:
    parameters = schema.get("parameters")
    if isinstance(parameters, dict):
        return parameters
    input_schema = schema.get("inputSchema")
    if isinstance(input_schema, dict):
        return input_schema
    function = schema.get("function")
    if isinstance(function, dict):
        nested = function.get("parameters")
        if isinstance(nested, dict):
            return nested
    return {"type": "object", "properties": {}}


def _search_tool_schema() -> _JsonObject:
    return {
        "type": "function",
        "name": MCP_TOOL_SEARCH_NAME,
        "description": (
            "Search the attached MCP servers for tools relevant to a task. "
            "Use this before mcp_tool_call. The result includes exact tool names "
            "and argument schemas; refine the query or server when needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Capability or action to find, such as 'list Google Drive files'."
                    ),
                },
                "server": {
                    "type": "string",
                    "description": "Optional exact MCP server name, such as 'google'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_RESULT_LIMIT,
                    "default": _DEFAULT_RESULT_LIMIT,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


def _call_tool_schema() -> _JsonObject:
    return {
        "type": "function",
        "name": MCP_TOOL_CALL_NAME,
        "description": (
            "Invoke one MCP tool returned by mcp_tool_search. Pass its exact "
            "server__tool name and arguments matching the discovered schema."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact namespaced tool name returned by mcp_tool_search.",
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments matching that tool's discovered schema.",
                    "additionalProperties": True,
                },
            },
            "required": ["name", "arguments"],
            "additionalProperties": False,
        },
    }
