"""Shared defaults for managed task agents."""

from __future__ import annotations

# Headless Claude SDK: honors the task-secretary YAML prompt as system_prompt.
DEFAULT_SECRETARY_HARNESS = "claude-sdk"
# Claude Code picker label "Sonnet 4.6" — use the ``sonnet`` alias, not ``claude-sonnet-4-6``.
DEFAULT_SECRETARY_MODEL = "sonnet"

# Task manager/worker/reviewer agents: Cursor native TUI.
DEFAULT_TASK_HARNESS = "cursor-native"
DEFAULT_TASK_MODEL = "composer-2.5"
DEFAULT_TASK_WORKSPACE = "~/"

AUTO_ROUTE_MIN_CONFIDENCE = 0.6
AUTO_ROUTE_MIN_MARGIN = 0.15
AUTO_ROUTE_MAX_CANDIDATES = 10

UNRECONCILED_EVENT_STATES = frozenset({"routed"})
GROUPING_EVENT_STATES = frozenset({"awaiting_grouping", "grouping_proposed"})
ORPHAN_EVENT_STATES = frozenset({"awaiting_grouping"})
ROUTING_PROPOSED_EVENT_STATE = "routing_proposed"
ROUTING_PROPOSED_ITEM_STATE = "routing_proposed"
DISPATCHABLE_ITEM_STATES = frozenset({"awaiting_user_ack", "approved"})


def resolve_task_harness(harness: str) -> str:
    """Return a runnable harness id for managed task agents."""
    if harness == "cursor":
        return "cursor-native"
    return harness
