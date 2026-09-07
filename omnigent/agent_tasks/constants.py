"""Shared defaults for managed task agents."""

from __future__ import annotations

import os

# Tag-similarity router (the ingress scorer + tag_matches search + broker
# suggested candidates). DEPRECATED: the manager now picks tasks from the
# rank-ordered roster injected into each notice and reads details via the
# batch endpoint. Flip to True (or set OMNIGENT_PUPPYGARDEN_TAG_ROUTER=1)
# to restore scorer auto-routing while it is being retired.
TAG_ROUTER_ENABLED = os.environ.get("OMNIGENT_PUPPYGARDEN_TAG_ROUTER", "").lower() in {
    "1",
    "true",
}

# How many candidate tasks the manager may inspect (batch-read details for)
# before deciding — guidance mirrored in the manager manual.
MANAGER_CANDIDATE_INSPECT_LIMIT = 10

# Token budget for the rank-ordered task roster injected into each manager
# notice (~4 chars/token). Roster lines beyond the budget are summarized
# with a count so the manager can list them via the tasks API.
MANAGER_ROSTER_MAX_TOKENS = 20_000

# Role engine/harness comes from the bound execution-target bundle
# (executor.config.harness) — roles carry no harness default and users
# cannot change the execution model. The role row's harness column is
# only a rarely-used explicit override and stays NULL for builtin roles.

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
# Events in these states are settled: nothing will process them again, and the
# event GC eventually purges them. Board summaries exclude them.
TERMINAL_EVENT_STATES = frozenset({"reconciled", "dismissed", "failed"})
CLASSIFIED_FYI_EVENT_STATE = "classified_fyi"
FYI_CLUSTER_OPEN_STATE = "pending"
DISPATCHABLE_ITEM_STATES = frozenset({"pending"})

# Manager sharing: how many live tasks one manager session may own before the
# attach flow spawns a new manager. Permissive at v2 launch; tune from logged
# attach decisions.
MANAGER_TASK_CAPACITY = 1_000_000
