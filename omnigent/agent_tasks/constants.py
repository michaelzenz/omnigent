"""Shared defaults for managed task agents."""

from __future__ import annotations

# Claude-native TUI: Bash for task APIs; ``permission_mode: auto`` in the YAML.
DEFAULT_SECRETARY_HARNESS = "claude-native"
# Claude Code picker label "Sonnet 4.6" — use the ``sonnet`` alias, not ``claude-sonnet-4-6``.
DEFAULT_SECRETARY_MODEL = "sonnet"

# Task manager/worker/reviewer agents: Cursor native TUI.
DEFAULT_TASK_HARNESS = "cursor-native"
DEFAULT_TASK_MODEL = "composer-2.5"
DEFAULT_TASK_WORKSPACE = "~/"

AUTO_ROUTE_MIN_CONFIDENCE = 0.6
AUTO_ROUTE_MIN_MARGIN = 0.15
AUTO_ROUTE_MAX_CANDIDATES = 10

SECRETARY_BATCH_MAX_SIZE = 10
MANAGER_BATCH_MAX_SIZE = 10

# Secretary packager: tag-overlap coefficient (|A ∩ B| / min(|A|, |B|)) at/above
# which two events join the same cluster. 0.8 ≈ "4 of 5 tags shared".
SECRETARY_TAG_SIMILARITY_THRESHOLD = 0.8
# How many candidate task ids to embed in a routed-cluster notice.
SECRETARY_CANDIDATE_LIMIT = 5

UNRECONCILED_EVENT_STATES = frozenset({"routed"})
AMBIGUOUS_EVENT_STATES = frozenset({"awaiting_grouping"})
CLASSIFIED_FYI_EVENT_STATE = "classified_fyi"
FYI_CLUSTER_OPEN_STATE = "awaiting_user_ack"
DISPATCHABLE_ITEM_STATES = frozenset({"awaiting_user_ack"})


def resolve_task_harness(harness: str) -> str:
    """Return a runnable harness id for managed task agents."""
    if harness == "cursor":
        return "cursor-native"
    if harness == "claude":
        return "claude-native"
    return harness
