"""Tests for lazy MCP schema discovery."""

from omnigent.tools.mcp_search import (
    MCP_TOOL_CALL_NAME,
    MCP_TOOL_SEARCH_NAME,
    openai_agents_lazy_tool_schemas,
    search_mcp_tool_schemas,
)


def _schema(name: str, description: str) -> dict[str, object]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    }


def test_openai_agents_lazy_schemas_keep_builtins_and_defer_mcp() -> None:
    schemas = [
        _schema("sys_os_read", "Read a file"),
        _schema("google__drive_file_list", "List files in Google Drive"),
        _schema("jira__jira_read_api_call", "Read Jira data"),
    ]

    result = openai_agents_lazy_tool_schemas(schemas)

    names = [schema["name"] for schema in result]
    assert names == ["sys_os_read", MCP_TOOL_SEARCH_NAME, MCP_TOOL_CALL_NAME]


def test_search_mcp_tool_schemas_ranks_name_and_description_matches() -> None:
    schemas = [
        _schema("jira__jira_read_api_call", "Read Jira issue data"),
        _schema("google__drive_file_list", "List files in Google Drive"),
        _schema("google__gmail_message_list", "List email messages"),
    ]

    result = search_mcp_tool_schemas(schemas, query="Google Drive files")

    assert result["tools"][0]["name"] == "google__drive_file_list"  # type: ignore[index]
    assert result["available_servers"] == ["google", "jira"]


def test_search_mcp_tool_schemas_honours_server_and_limit() -> None:
    schemas = [
        _schema("google__drive_file_list", "List files"),
        _schema("google__drive_file_get", "Get file"),
        _schema("jira__jira_read_api_call", "Read data"),
    ]

    result = search_mcp_tool_schemas(
        schemas,
        query="file",
        server="google",
        limit=1,
    )

    tools = result["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 1
    assert str(tools[0]["name"]).startswith("google__")
