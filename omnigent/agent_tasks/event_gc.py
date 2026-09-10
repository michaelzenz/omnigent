"""Background GC for task events and agent queue items.

Periodically deletes old reconciled/dismissed events and completed queue
items so large worker-output payloads do not accumulate indefinitely.
Configurable via ``~/.omnigent/config.yaml``:

.. code-block:: yaml

    server:
      event_gc:
        interval_s: 3600          # run every hour
        reconciled_retention_s: 1814400   # 3 weeks
        stale_routed_retention_s: 604800  # 7 days
        queue_retention_s: 1814400        # 3 weeks
        adoption_proposal_retention_s: 86400  # 1 day
        resolved_archive_after_s: 604800  # 1 week; 0 disables
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 3600.0
_DEFAULT_RECONCILED_RETENTION_S = 1_814_400.0  # 3 weeks
_DEFAULT_STALE_ROUTED_RETENTION_S = 604_800.0  # 7 days
_DEFAULT_QUEUE_RETENTION_S = 1_814_400.0  # 3 weeks
_DEFAULT_ADOPTION_PROPOSAL_RETENTION_S = 86_400.0  # 1 day
# Auto-archive agent-resolved tasks after 1 week in that state.
_DEFAULT_RESOLVED_ARCHIVE_AFTER_S = 604_800.0


# Thin indirections so tests can patch the loop's sleep/clock without globally
# clobbering the real ``asyncio``/``time`` module singletons.
async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _now() -> int:
    return int(time.time())


@dataclass(frozen=True)
class EventGcConfig:
    interval_s: float
    reconciled_retention_s: float
    stale_routed_retention_s: float
    queue_retention_s: float
    adoption_proposal_retention_s: float = _DEFAULT_ADOPTION_PROPOSAL_RETENTION_S
    resolved_archive_after_s: float = _DEFAULT_RESOLVED_ARCHIVE_AFTER_S


def load_event_gc_config(config_path: Path = CONFIG_PATH) -> EventGcConfig:
    interval_s = _DEFAULT_INTERVAL_S
    reconciled_retention_s = _DEFAULT_RECONCILED_RETENTION_S
    stale_routed_retention_s = _DEFAULT_STALE_ROUTED_RETENTION_S
    queue_retention_s = _DEFAULT_QUEUE_RETENTION_S
    adoption_proposal_retention_s = _DEFAULT_ADOPTION_PROPOSAL_RETENTION_S
    resolved_archive_after_s = _DEFAULT_RESOLVED_ARCHIVE_AFTER_S
    if config_path.exists():
        try:
            with config_path.open(encoding="utf-8") as handle:
                cfg = yaml.safe_load(handle) or {}
        except OSError:
            cfg = {}
        if isinstance(cfg, dict):
            server_section = cfg.get("server")
            if isinstance(server_section, dict):
                gc_section = server_section.get("event_gc")
                if isinstance(gc_section, dict):
                    v = _positive_float(gc_section.get("interval_s"))
                    if v is not None:
                        interval_s = v
                    v = _positive_float(gc_section.get("reconciled_retention_s"))
                    if v is not None:
                        reconciled_retention_s = v
                    v = _positive_float(gc_section.get("stale_routed_retention_s"))
                    if v is not None:
                        stale_routed_retention_s = v
                    v = _positive_float(gc_section.get("queue_retention_s"))
                    if v is not None:
                        queue_retention_s = v
                    v = _positive_float(gc_section.get("adoption_proposal_retention_s"))
                    if v is not None:
                        adoption_proposal_retention_s = v
                    # 0 disables auto-archiving; accept non-negative here.
                    v = gc_section.get("resolved_archive_after_s")
                    if isinstance(v, (int, float)) and v >= 0:
                        resolved_archive_after_s = float(v)
    return EventGcConfig(
        interval_s=interval_s,
        reconciled_retention_s=reconciled_retention_s,
        stale_routed_retention_s=stale_routed_retention_s,
        queue_retention_s=queue_retention_s,
        adoption_proposal_retention_s=adoption_proposal_retention_s,
        resolved_archive_after_s=resolved_archive_after_s,
    )


def _positive_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


async def run_event_gc(
    task_event_store: TaskEventStore,
    agent_queue_store: AgentQueueStore,
    *,
    task_store: TaskStore | None = None,
    config: EventGcConfig | None = None,
    config_path: Path = CONFIG_PATH,
) -> None:
    """Periodically purge old events, queue items, and stale resolved tasks
    until cancelled."""
    if config is None:
        config = load_event_gc_config(config_path)
    while True:
        await _sleep(config.interval_s)
        try:
            now = _now()
            n_reconciled = task_event_store.purge_old_events(
                before_ts=now - int(config.reconciled_retention_s),
                states=["reconciled", "dismissed", "failed"],
            )
            # A broadcast canonical must outlive its fan-out copies; purging it
            # earlier would let a replay dedup-miss and re-deliver. Use the
            # longest window any child can live under.
            n_broadcast = task_event_store.purge_old_events(
                before_ts=now
                - int(max(config.reconciled_retention_s, config.stale_routed_retention_s)),
                states=["broadcast"],
            )
            n_stale = task_event_store.purge_old_events(
                before_ts=now - int(config.stale_routed_retention_s),
                states=["routed"],
            )
            n_proposals = task_event_store.purge_old_events(
                before_ts=now - int(config.adoption_proposal_retention_s),
                states=["routed"],
                event_type="session.adoption",
            )
            n_items = agent_queue_store.purge_old_items(
                before_ts=now - int(config.queue_retention_s),
                states=["done", "cancelled"],
            )
            # Auto-archive agent-resolved tasks past the retention window.
            # Skipped when no store is wired or the retention is set to 0.
            n_archived = 0
            if task_store is not None and config.resolved_archive_after_s > 0:
                n_archived = task_store.archive_expired_resolved(
                    before_ts=now - int(config.resolved_archive_after_s),
                )
            if n_reconciled or n_broadcast or n_stale or n_proposals or n_items or n_archived:
                _logger.info(
                    "event GC: purged %d reconciled/dismissed events, %d broadcast events, "
                    "%d stale routed events, %d adoption proposals, %d queue items; "
                    "auto-archived %d agent-resolved tasks",
                    n_reconciled,
                    n_broadcast,
                    n_stale,
                    n_proposals,
                    n_items,
                    n_archived,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _logger.warning("event GC tick failed", exc_info=True)
