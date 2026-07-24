"""Shared defaults for managed task agents."""

from __future__ import annotations

DEFAULT_TASK_HARNESS = "cursor"
DEFAULT_TASK_MODEL = "composer-2.5"

AUTO_ROUTE_MIN_CONFIDENCE = 0.6
AUTO_ROUTE_MIN_MARGIN = 0.15
AUTO_ROUTE_MAX_CANDIDATES = 10

UNRECONCILED_EVENT_STATES = frozenset({"routed"})
GROUPING_EVENT_STATES = frozenset({"awaiting_grouping", "grouping_proposed"})
DISPATCHABLE_ITEM_STATES = frozenset({"awaiting_user_ack", "approved"})
