"""Shared defaults for managed task agents."""

from __future__ import annotations

# Broker: triages and routes stalled events.
DEFAULT_BROKER_HARNESS = "openai-agents"
DEFAULT_BROKER_MODEL = "databricks-glm-5-2"

# Task manager/worker/reviewer agents.
DEFAULT_TASK_HARNESS = "openai-agents"
DEFAULT_TASK_MODEL = "databricks-glm-5-2"
DEFAULT_TASK_WORKSPACE = "~/"

AUTO_ROUTE_MIN_CONFIDENCE = 0.6
AUTO_ROUTE_MIN_MARGIN = 0.15
AUTO_ROUTE_MAX_CANDIDATES = 10

BROKER_BATCH_MAX_SIZE = 10
MANAGER_BATCH_MAX_SIZE = 10

# Minimum age (seconds) before any routed event is eligible for packaging.
# Session events wait this long so small bursts batch together.
SESSION_EVENT_COOLDOWN_S = 180

# Broker packager: tag-overlap coefficient (|A ∩ B| / min(|A|, |B|)) at/above
# which two events join the same cluster. 0.8 ≈ "4 of 5 tags shared".
BROKER_TAG_SIMILARITY_THRESHOLD = 0.8
# How many candidate task ids to embed in a routed-cluster notice.
BROKER_CANDIDATE_LIMIT = 5

UNRECONCILED_EVENT_STATES = frozenset({"routed"})
AMBIGUOUS_EVENT_STATES = frozenset({"awaiting_grouping"})
CLASSIFIED_FYI_EVENT_STATE = "classified_fyi"
FYI_CLUSTER_OPEN_STATE = "pending"
DISPATCHABLE_ITEM_STATES = frozenset({"pending"})

# Manager sharing: how many live tasks one manager session may own before the
# attach flow spawns a new manager. Permissive at v2 launch; tune from logged
# attach decisions.
MANAGER_TASK_CAPACITY = 1_000_000


def resolve_task_harness(harness: str) -> str:
    """Return a runnable harness id for managed task agents."""
    if harness == "cursor":
        return "cursor-native"
    if harness == "claude":
        return "claude-native"
    return harness
