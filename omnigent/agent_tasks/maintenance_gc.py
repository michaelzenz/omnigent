"""Background maintenance for the pmv2 task system.

Periodically deletes old reconciled/dismissed events and completed queue
items so large worker-output payloads do not accumulate indefinitely, and
auto-archives agent-resolved tasks past the retention window.
Configurable via ``~/.omnigent/config.yaml``:

.. code-block:: yaml

    server:
      maintenance_gc:
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
class MaintenanceGcConfig:
    interval_s: float
    reconciled_retention_s: float
    stale_routed_retention_s: float
    queue_retention_s: float
    adoption_proposal_retention_s: float = _DEFAULT_ADOPTION_PROPOSAL_RETENTION_S
    resolved_archive_after_s: float = _DEFAULT_RESOLVED_ARCHIVE_AFTER_S


def load_maintenance_gc_config(config_path: Path = CONFIG_PATH) -> MaintenanceGcConfig:
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
                gc_section = server_section.get("maintenance_gc")
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
    return MaintenanceGcConfig(
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


@dataclass(frozen=True)
class _EventPurgeCounts:
    """Purge counts from the event-store sweep, by event category."""

    reconciled: int
    broadcast: int
    stale_routed: int
    adoption_proposals: int

    @property
    def total(self) -> int:
        return self.reconciled + self.broadcast + self.stale_routed + self.adoption_proposals


def _gc_task_events(
    task_event_store: TaskEventStore,
    config: MaintenanceGcConfig,
    now: int,
) -> _EventPurgeCounts:
    """Purge old task events (reconciled/dismissed, broadcast, stale routed,
    adoption proposals)."""
    n_reconciled = task_event_store.purge_old_events(
        before_ts=now - int(config.reconciled_retention_s),
        states=["reconciled", "dismissed", "failed"],
    )
    # A broadcast canonical must outlive its fan-out copies; purging it
    # earlier would let a replay dedup-miss and re-deliver. Use the longest
    # window any child can live under.
    n_broadcast = task_event_store.purge_old_events(
        before_ts=now - int(max(config.reconciled_retention_s, config.stale_routed_retention_s)),
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
    return _EventPurgeCounts(
        reconciled=n_reconciled,
        broadcast=n_broadcast,
        stale_routed=n_stale,
        adoption_proposals=n_proposals,
    )


def _gc_queue_items(
    agent_queue_store: AgentQueueStore,
    config: MaintenanceGcConfig,
    now: int,
) -> int:
    """Purge old completed agent-queue items. Returns the count purged."""
    return agent_queue_store.purge_old_items(
        before_ts=now - int(config.queue_retention_s),
        states=["done", "cancelled"],
    )


def _archive_stale_resolved(
    task_store: TaskStore | None,
    config: MaintenanceGcConfig,
    now: int,
) -> int:
    """Auto-archive agent-resolved tasks past the retention window.

    Skipped when no task store is wired or the retention is set to 0
    (disabled). Returns the number archived.
    """
    if task_store is None or config.resolved_archive_after_s <= 0:
        return 0
    return task_store.archive_expired_resolved(
        before_ts=now - int(config.resolved_archive_after_s),
    )


async def run_maintenance_gc(
    task_event_store: TaskEventStore,
    agent_queue_store: AgentQueueStore,
    *,
    task_store: TaskStore | None = None,
    config: MaintenanceGcConfig | None = None,
    config_path: Path = CONFIG_PATH,
) -> None:
    """Periodically purge old events, queue items, and stale resolved tasks
    until cancelled."""
    if config is None:
        config = load_maintenance_gc_config(config_path)
    while True:
        await _sleep(config.interval_s)
        try:
            now = _now()
            n_events = _gc_task_events(task_event_store, config, now)
            n_items = _gc_queue_items(agent_queue_store, config, now)
            n_archived = _archive_stale_resolved(task_store, config, now)
            if n_events or n_items or n_archived:
                _logger.info(
                    "maintenance GC: purged %d events (%d reconciled/dismissed, "
                    "%d broadcast, %d stale routed, %d adoption proposals), "
                    "%d queue items; auto-archived %d agent-resolved tasks",
                    n_events,
                    n_events.reconciled,
                    n_events.broadcast,
                    n_events.stale_routed,
                    n_events.adoption_proposals,
                    n_items,
                    n_archived,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _logger.warning("maintenance GC tick failed", exc_info=True)
