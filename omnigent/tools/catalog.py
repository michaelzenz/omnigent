"""Canonical catalog of framework tools for the admin Tools panel.

Each entry carries the stable tool name (the preference key), a
human-readable title, short description, and presentation group.
The catalog is backend-owned so the API, enforcement layer, and UI
all share one source of truth. New tools default to enabled; the
store persists only *disabled* names.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCatalogEntry:
    name: str
    title: str
    description: str
    group: str


@dataclass(frozen=True)
class ToolGroup:
    id: str
    title: str
    order: int


GROUPS: list[ToolGroup] = [
    ToolGroup(id="skills", title="Skills", order=10),
    ToolGroup(id="files_content", title="Files & content", order=20),
    ToolGroup(id="pi_file_interaction", title="File interaction — Pi", order=25),
    ToolGroup(id="agents_sessions", title="Agents & sessions", order=30),
    ToolGroup(id="computer_terminal", title="Computer & terminal", order=40),
    ToolGroup(id="legacy_os_interaction", title="Legacy OS interaction", order=45),
    ToolGroup(id="automation", title="Automation", order=50),
    ToolGroup(id="web_research", title="Web & research", order=60),
    ToolGroup(id="reviews", title="Reviews", order=70),
    ToolGroup(id="policies", title="Policies", order=80),
    ToolGroup(id="memory", title="Long-term memory", order=90),
    ToolGroup(id="mcp", title="MCP access", order=100),
    ToolGroup(id="models", title="Model discovery", order=110),
    ToolGroup(id="puppygarden", title="PuppyGarden", order=120),
]

TOOLS: list[ToolCatalogEntry] = [
    # Skills
    ToolCatalogEntry("load_skill", "Load skills", "Load a skill's instructions by name.", "skills"),
    ToolCatalogEntry("read_skill_file", "Read skill files", "Read supporting files from an installed skill.", "skills"),
    ToolCatalogEntry("update_skill", "Update skills", "Update text files in an existing skill.", "skills"),
    ToolCatalogEntry("write_skill", "Create skills", "Create a new skill directory.", "skills"),

    # Files & content
    ToolCatalogEntry("upload_file", "Upload files", "Upload a file from the workspace for download.", "files_content"),
    ToolCatalogEntry("list_files", "List files", "List uploaded or created files.", "files_content"),
    ToolCatalogEntry("download_file", "Download files", "Download a file by its ID to the workspace.", "files_content"),
    ToolCatalogEntry("search_conversations", "Search conversations", "Search past conversations for relevant information.", "files_content"),
    ToolCatalogEntry("export_agent", "Export agents", "Copy a generated agent directory to a target path.", "files_content"),

    # Agents & sessions
    ToolCatalogEntry("sys_session_create", "Create agent sessions", "Spawn a child session from an agent.", "agents_sessions"),
    ToolCatalogEntry("sys_session_send", "Send to sub-agents", "Send a message to an existing child session.", "agents_sessions"),
    ToolCatalogEntry("sys_session_close", "Close sub-agent sessions", "Tombstone a sibling sub-agent session.", "agents_sessions"),
    ToolCatalogEntry("sys_session_list", "List sessions", "List sessions in two views: sub-agents and global.", "agents_sessions"),
    ToolCatalogEntry("sys_session_get_history", "Read session history", "Read recent items from a session's conversation.", "agents_sessions"),
    ToolCatalogEntry("sys_session_get_info", "Get session info", "Return a session's metadata and lifecycle status.", "agents_sessions"),
    ToolCatalogEntry("sys_session_share", "Share sessions", "Grant a user access to a session.", "agents_sessions"),
    ToolCatalogEntry("sys_session_rename", "Rename sessions", "Rename the current session with a short title.", "agents_sessions"),
    ToolCatalogEntry("sys_agent_list", "List available agents", "List launchable agents across built-in, session, and local configs.", "agents_sessions"),
    ToolCatalogEntry("sys_agent_get", "Get agent metadata", "Return the agent metadata bound to a session.", "agents_sessions"),
    ToolCatalogEntry("sys_agent_download", "Download agent bundles", "Download a session's agent bundle for inspection or forking.", "agents_sessions"),

    # File interaction — Pi (new Pi-compatible tools)
    ToolCatalogEntry("read", "Read files", "Read a text file with inclusive start/end line ranges.", "pi_file_interaction"),
    ToolCatalogEntry("write", "Write files", "Write full file contents, creating parent directories.", "pi_file_interaction"),
    ToolCatalogEntry("edit", "Edit files", "Perform exact text replacements in a file, with batch support.", "pi_file_interaction"),
    ToolCatalogEntry("bash", "Run shell commands", "Run a shell command in the OS environment.", "pi_file_interaction"),
    ToolCatalogEntry("grep", "Search file contents", "Search file contents for a pattern with context and glob filtering.", "pi_file_interaction"),
    ToolCatalogEntry("find", "Find files", "Find files and directories matching a glob pattern.", "pi_file_interaction"),
    ToolCatalogEntry("ls", "List directories", "List directory entries (non-recursive), including dotfiles.", "pi_file_interaction"),

    # Computer & terminal
    ToolCatalogEntry("sys_terminal_launch", "Launch terminal sessions", "Launch a named terminal (tmux session).", "computer_terminal"),
    ToolCatalogEntry("sys_terminal_send", "Send to terminals", "Send text and/or key strokes to a running terminal.", "computer_terminal"),
    ToolCatalogEntry("sys_terminal_read", "Read terminal output", "Capture the visible pane plus optional scrollback.", "computer_terminal"),
    ToolCatalogEntry("sys_terminal_list", "List terminal sessions", "List the conversation's active terminal sessions.", "computer_terminal"),
    ToolCatalogEntry("sys_terminal_close", "Close terminal sessions", "Close a running terminal session.", "computer_terminal"),

    # Legacy OS interaction
    ToolCatalogEntry("sys_os_read", "Read files (legacy)", "Read a text file from the OS environment.", "legacy_os_interaction"),
    ToolCatalogEntry("sys_os_write", "Write files (legacy)", "Write full contents of a text file in the OS environment.", "legacy_os_interaction"),
    ToolCatalogEntry("sys_os_edit", "Edit files (legacy)", "Perform exact text replacements in a file in the OS environment.", "legacy_os_interaction"),
    ToolCatalogEntry("sys_os_shell", "Run shell (legacy)", "Run a shell command in the OS environment.", "legacy_os_interaction"),

    # Automation
    ToolCatalogEntry("sys_call_async", "Dispatch async tasks", "Dispatch a local Python tool as a background task.", "automation"),
    ToolCatalogEntry("sys_cancel_async", "Cancel async tasks", "Cancel a previously dispatched async task.", "automation"),
    ToolCatalogEntry("sys_read_inbox", "Read async inbox", "Drain the inbox of completed async-work payloads.", "automation"),
    ToolCatalogEntry("sys_cancel_task", "Cancel running tasks", "Cancel a running background task by task ID.", "automation"),
    ToolCatalogEntry("sys_timer_set", "Set timers", "Schedule a timer that fires after a delay.", "automation"),
    ToolCatalogEntry("sys_timer_cancel", "Cancel timers", "Cancel a previously scheduled timer.", "automation"),
    ToolCatalogEntry("sys_scheduled_task_create", "Create scheduled tasks", "Create a recurring agent run on a schedule (RRULE).", "automation"),
    ToolCatalogEntry("sys_scheduled_task_list", "List scheduled tasks", "List scheduled tasks with their schedules and state.", "automation"),
    ToolCatalogEntry("sys_scheduled_task_update", "Update scheduled tasks", "Update a scheduled task's mutable fields.", "automation"),
    ToolCatalogEntry("sys_scheduled_task_delete", "Delete scheduled tasks", "Delete a scheduled task so it no longer fires.", "automation"),

    # Web & research
    ToolCatalogEntry("web_search", "Web search", "Search the web for information.", "web_research"),
    ToolCatalogEntry("web_fetch", "Web fetch", "Fetch and extract content from a URL.", "web_research"),
    ToolCatalogEntry("nimble_research", "Nimble research", "Run a Nimble research task.", "web_research"),
    ToolCatalogEntry("nimble_extract", "Nimble extract", "Extract structured data from a URL via Nimble.", "web_research"),
    ToolCatalogEntry("browser_navigate", "Browser navigation", "Open or navigate the embedded browser to a URL.", "web_research"),
    ToolCatalogEntry("browser_snapshot", "Browser snapshot", "Capture an accessibility-tree snapshot of the browser.", "web_research"),
    ToolCatalogEntry("browser_click", "Browser click", "Click an element in the embedded browser.", "web_research"),
    ToolCatalogEntry("browser_type", "Browser type", "Type text into a browser input element.", "web_research"),
    ToolCatalogEntry("browser_screenshot", "Browser screenshot", "Capture a PNG screenshot of the browser pane.", "web_research"),

    # Reviews
    ToolCatalogEntry("list_comments", "List review comments", "List review comments for the current session.", "reviews"),
    ToolCatalogEntry("update_comment", "Update review comments", "Update the status of a review comment.", "reviews"),

    # Policies
    ToolCatalogEntry("sys_add_policy", "Add runtime policies", "Add a runtime policy to the current session.", "policies"),
    ToolCatalogEntry("sys_policy_registry", "List policy templates", "List available built-in policy templates.", "policies"),

    # Long-term memory
    ToolCatalogEntry("hindsight_retain", "Hindsight retain", "Store information to long-term memory.", "memory"),
    ToolCatalogEntry("hindsight_recall", "Hindsight recall", "Recall information from long-term memory.", "memory"),
    ToolCatalogEntry("hindsight_reflect", "Hindsight reflect", "Reflect on and consolidate long-term memory.", "memory"),

    # MCP access
    ToolCatalogEntry("mcp_tool_search", "Search MCP tools", "Search attached MCP servers for relevant tools.", "mcp"),
    ToolCatalogEntry("mcp_tool_call", "Call MCP tools", "Invoke an MCP tool returned by mcp_tool_search.", "mcp"),

    # Model discovery
    ToolCatalogEntry("sys_list_models", "List available models", "List models available to sub-agent workers.", "models"),
    ToolCatalogEntry("sys_advise_models", "Advise on models", "Provide model routing advice.", "models"),

    # PuppyGarden
    ToolCatalogEntry("puppygarden_api", "PuppyGarden task API", "Call a PuppyGarden task API endpoint.", "puppygarden"),
]

# Quick lookup maps
TOOL_BY_NAME: dict[str, ToolCatalogEntry] = {t.name: t for t in TOOLS}
GROUP_BY_ID: dict[str, ToolGroup] = {g.id: g for g in GROUPS}
ALL_TOOL_NAMES: frozenset[str] = frozenset(t.name for t in TOOLS)

# Tools that are conditionally available (require optional deps or config).
# These appear in the catalog but may not be registered for every agent.
CONDITIONAL_TOOLS: frozenset[str] = frozenset({
    "hindsight_retain",
    "hindsight_recall",
    "hindsight_reflect",
    "nimble_research",
    "nimble_extract",
    "web_search",
    "web_fetch",
})


def get_catalog_response(disabled_tools: frozenset[str]) -> dict:
    """Build the API response combining the catalog with enabled state."""
    return {
        "object": "tool_preferences",
        "groups": [
            {"id": g.id, "title": g.title, "order": g.order}
            for g in GROUPS
        ],
        "tools": [
            {
                "name": t.name,
                "title": t.title,
                "description": t.description,
                "group": t.group,
                "enabled": t.name not in disabled_tools,
                "available": t.name not in CONDITIONAL_TOOLS,
            }
            for t in TOOLS
        ],
        "disabled_tools": sorted(disabled_tools),
    }
