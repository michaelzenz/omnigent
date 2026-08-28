"""Serve HTML built-in tool.

Stores HTML content (inline or from a workspace file) via the file
store + artifact store, and returns a preview URL that the agent
includes in its chat response. The frontend detects the URL and
opens the HTML in an in-app preview pane (sandboxed iframe) that
replaces the chat column.

Files are session-scoped and garbage-collected when the session
is deleted (via FileStore.delete_all_for_session).
"""

from __future__ import annotations

import json
from typing import Any

from omnigent.tools.base import Tool, ToolContext
from omnigent.tools.builtins._arguments import parse_json_object_arguments
from omnigent.tools.builtins.upload_file import safe_resolve

_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "serve_html",
        "description": (
            "Serve HTML content for the user to preview in-app. "
            "Provide either inline HTML via 'html' or a workspace file path via 'path'. "
            "Returns a preview_url the user can click to see the rendered HTML. "
            "The HTML is sandboxed and session-scoped — it is deleted when the session ends."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "html": {
                    "type": "string",
                    "description": (
                        "Inline HTML content to serve. "
                        "Use this for generated prototypes, dashboards, or demos."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path to an HTML file in the workspace, "
                        "e.g. 'output/dashboard.html'. "
                        "Alternative to 'html' — use whichever is convenient."
                    ),
                },
            },
        },
    },
}


class ServeHtmlTool(Tool):
    """Serve HTML content for in-app preview."""

    @classmethod
    def name(cls) -> str:
        return "serve_html"

    @classmethod
    def description(cls) -> str:
        return (
            "Serve HTML content for the user to preview in-app. "
            "Provide either inline HTML via 'html' or a workspace file path via 'path'. "
            "Returns a preview_url the user can click to see the rendered HTML."
        )

    def get_schema(self) -> dict[str, Any]:
        return _SCHEMA

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        parsed, error = parse_json_object_arguments(arguments)
        if error is not None:
            return f"Error: {error}"
        assert parsed is not None

        html_content = parsed.get("html", "")
        rel_path = parsed.get("path", "")

        if not html_content and not rel_path:
            return "Error: provide either 'html' or 'path'"

        if html_content:
            if not isinstance(html_content, str):
                return "Error: 'html' must be a string"
            data = html_content.encode("utf-8")
        else:
            if not isinstance(rel_path, str) or not rel_path:
                return "Error: 'path' must be a non-empty string"
            if ctx.workspace is None:
                return "Error: no workspace available"
            try:
                resolved = safe_resolve(rel_path, ctx.workspace)
            except ValueError as exc:
                return f"Error: {exc}"
            if not resolved.is_file():
                return f"Error: file not found: {rel_path}"
            data = resolved.read_bytes()

        return _serve(data, session_id=ctx.conversation_id)


def _serve(data: bytes, *, session_id: str | None) -> str:
    from omnigent.runtime import get_artifact_store, get_file_store

    file_store = get_file_store()
    artifact_store = get_artifact_store()

    if file_store is None or artifact_store is None:
        return "Error: file store not available"
    if session_id is None:
        return "Error: serve_html requires a session id"

    file_record = file_store.create(
        filename="preview.html",
        bytes=len(data),
        content_type="text/html",
        session_id=session_id,
    )
    artifact_store.put(file_record.id, data)

    preview_url = f"/v1/sessions/{session_id}/resources/files/{file_record.id}/preview"

    return json.dumps(
        {
            "preview_url": preview_url,
            "file_id": file_record.id,
            "bytes": len(data),
        }
    )
