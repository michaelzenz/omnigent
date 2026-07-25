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

DISTRIBUTOR_AGENT_ENABLED_ENV = "DISTRIBUTOR_AGENT_ENABLED"
DISTRIBUTOR_BATCH_DEBOUNCE_SECONDS = 2.0
DISTRIBUTOR_BATCH_MAX_SIZE = 10
DISTRIBUTOR_ESCALATION_SECONDS = 60.0

UNRECONCILED_EVENT_STATES = frozenset({"routed"})
ORPHAN_EVENT_STATES = frozenset({"awaiting_grouping"})
ROUTING_PROPOSED_EVENT_STATE = "routing_proposed"
ROUTING_PROPOSED_ITEM_STATE = "routing_proposed"
CLASSIFIED_FYI_EVENT_STATE = "classified_fyi"
FYI_CLUSTER_OPEN_STATE = "awaiting_user_ack"
DISPATCHABLE_ITEM_STATES = frozenset({"awaiting_user_ack", "approved"})


def resolve_task_harness(harness: str) -> str:
    """Return a runnable harness id for managed task agents."""
    if harness == "cursor":
        return "cursor-native"
    return harness


def distributor_agent_enabled() -> bool:
    """Return whether stalled events enqueue for the task-distributor agent."""
    from omnigent.server.auth import env_var_is_truthy

    return env_var_is_truthy(DISTRIBUTOR_AGENT_ENABLED_ENV, default=False)
