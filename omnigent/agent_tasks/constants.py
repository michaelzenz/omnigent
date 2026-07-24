"""Shared defaults for managed task agents."""

from __future__ import annotations

# ``cursor-native`` TUI harness — same as the main-page Cursor agent. Puppy
# Garden wires the embedded terminal UI so TUI output mirrors into chat.
DEFAULT_TASK_HARNESS = "cursor-native"
DEFAULT_TASK_MODEL = "composer-2.5"
DEFAULT_TASK_WORKSPACE = "~/"

AUTO_ROUTE_MIN_CONFIDENCE = 0.6
AUTO_ROUTE_MIN_MARGIN = 0.15
AUTO_ROUTE_MAX_CANDIDATES = 10

UNRECONCILED_EVENT_STATES = frozenset({"routed"})
GROUPING_EVENT_STATES = frozenset({"awaiting_grouping", "grouping_proposed"})
DISPATCHABLE_ITEM_STATES = frozenset({"awaiting_user_ack", "approved"})


def resolve_task_harness(harness: str) -> str:
    """Return a runnable Cursor harness for managed task agents."""
    if harness in ("cursor-native", "cursor"):
        return "cursor-native"
    return harness
