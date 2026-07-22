"""Shared defaults for managed task agents."""

from __future__ import annotations

# Headless cursor-agent CLI harness and Composer model for cost-efficient testing.
DEFAULT_TASK_HARNESS = "cursor"
DEFAULT_TASK_MODEL = "composer-2.5"

AUTO_ROUTE_MIN_CONFIDENCE = 0.6
AUTO_ROUTE_MIN_MARGIN = 0.15
AUTO_ROUTE_MAX_CANDIDATES = 10

# Inbound events the manager must triage before marking processed.
MANAGER_TRIAGE_EVENT_STATES = frozenset({"awaiting_manager_triage", "routed"})

# Event states that accept worker dispatch.
DISPATCHABLE_EVENT_STATES = frozenset({"awaiting_manager_triage", "routed", "received"})
