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

_logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 3600.0
_DEFAULT_RECONCILED_RETENTION_S = 1_814_400.0  # 3 weeks
_DEFAULT_STALE_ROUTED_RETENTION_S = 604_800.0  # 7 days
_DEFAULT_QUEUE_RETENTION_S = 1_814_400.0  # 3 weeks
_DEFAULT_ADOPTION_PROPOSAL_RETENTION_S = 86_400.0  # 1 day


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


def load_event_gc_config(config_path: Path = CONFIG_PATH) -> EventGcConfig:
    interval_s = _DEFAULT_INTERVAL_S
    reconciled_retention_s = _DEFAULT_RECONCILED_RETENTION_S
    stale_routed_retention_s = _DEFAULT_STALE_ROUTED_RETENTION_S
    queue_retention_s = _DEFAULT_QUEUE_RETENTION_S
    adoption_proposal_retention_s = _DEFAULT_ADOPTION_PROPOSAL_RETENTION_S
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
    return EventGcConfig(
        interval_s=interval_s,
        reconciled_retention_s=reconciled_retention_s,
        stale_routed_retention_s=stale_routed_retention_s,
        queue_retention_s=queue_retention_s,
        adoption_proposal_retention_s=adoption_proposal_retention_s,
    )


def _positive_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


async def run_event_gc(
    task_event_store: TaskEventStore,
    agent_queue_store: AgentQueueStore,
    *,
    config: EventGcConfig | None = None,
    config_path: Path = CONFIG_PATH,
) -> None:
    """Periodically purge old events and queue items until cancelled."""
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
            if n_reconciled or n_broadcast or n_stale or n_proposals or n_items:
                _logger.info(
                    "event GC: purged %d reconciled/dismissed events, %d broadcast events, "
                    "%d stale routed events, %d adoption proposals, %d queue items",
                    n_reconciled,
                    n_broadcast,
                    n_stale,
                    n_proposals,
                    n_items,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.warning("event GC tick failed", exc_info=True)
