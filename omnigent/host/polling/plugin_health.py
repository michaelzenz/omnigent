"""Per-plugin health tracking + reporting for host pollers.

Each poller keeps a :class:`PluginHealthTracker` and updates it after every
plugin run / skip. At the end of each ``poll_once`` it calls
:meth:`PluginHealthTracker.maybe_post`, which POSTs the snapshot to the server
when the snapshot changed since the last post, or as a heartbeat at least every
``_HEARTBEAT_S`` (so a steady-healthy plugin still proves the host is alive).

The server stores the snapshot in-memory keyed by ``(host_id, plugin_name)``;
the glossaries board reads it back. Health is a current snapshot, not history.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from omnigent.host.polling.context import PollContext

_logger = logging.getLogger(__name__)

# POST on change, plus a heartbeat at least this often so the server knows a
# steady-healthy host is still alive (not just "unchanged").
_HEARTBEAT_S = 180.0
# Truncate last_error so a verbose traceback doesn't bloat the snapshot.
_MAX_ERROR_LEN = 500
_HEALTH_PATH = "/v1/agent-tasks/script-plugins/health"

PluginKind = Literal["poll", "timer"]
# outcome strings — the board maps these to a status.
Outcome = Literal[
    "ok",
    "exit_nonzero",
    "timeout",
    "start_failed",
    "skipped_singleton",
    "skipped_config",
    # timer-only, non-firing states (no failure accounting):
    "scheduled",
    "already_fired",
]


@dataclass
class PluginHealthRecord:
    """One plugin's latest run outcome on this host."""

    name: str
    kind: PluginKind
    outcome: Outcome
    last_run_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    singleton_skipped: bool = False
    # A non-fatal advisory, e.g. "duplicate plugin name across scan roots".
    warning: str | None = None
    # poll
    interval_s: float | None = None
    # timer
    fire_at: float | None = None
    fired_at: float | None = None


def _truncate(error: str | None) -> str | None:
    if error is None:
        return None
    error = error.strip()
    if len(error) <= _MAX_ERROR_LEN:
        return error or None
    return error[:_MAX_ERROR_LEN].rstrip() + "…"


@dataclass
class PluginHealthTracker:
    """In-memory per-plugin health, posted to the server on change/heartbeat."""

    kind: PluginKind
    _records: dict[str, PluginHealthRecord] = field(default_factory=dict)
    _last_signature: str = ""
    _last_post_at: float = 0.0
    # Per-plugin advisory warnings (e.g. duplicate name across scan roots).
    # Replaced wholesale by :meth:`set_warnings` each scan so stale warnings
    # clear automatically. Carried into every record so the board can show it.
    _warnings: dict[str, str] = field(default_factory=dict)

    def set_warnings(self, names: set[str], message: str) -> None:
        """Set the advisory warning for exactly ``names``, clearing others.

        Called once per scan with the set of colliding plugin names. Plugins
        not in ``names`` get their warning cleared (in existing records too),
        so a resolved collision stops warning on the next board refresh.
        """
        self._warnings = dict.fromkeys(names, message)
        for name, rec in self._records.items():
            rec.warning = message if name in names else None

    def record_run(
        self,
        name: str,
        *,
        outcome: Outcome,
        error: str | None = None,
        interval_s: float | None = None,
        fire_at: float | None = None,
        fired_at: float | None = None,
    ) -> None:
        """Record the outcome of an actual run (success or failure)."""
        now = time.time()
        prev = self._records.get(name)
        consecutive = prev.consecutive_failures if prev else 0
        if outcome == "ok":
            consecutive = 0
            last_success = now
            last_failure = prev.last_failure_at if prev else None
        else:
            consecutive += 1
            last_success = prev.last_success_at if prev else None
            last_failure = now
        self._records[name] = PluginHealthRecord(
            name=name,
            kind=self.kind,
            outcome=outcome,
            last_run_at=now,
            last_success_at=last_success,
            last_failure_at=last_failure,
            last_error=_truncate(error),
            consecutive_failures=consecutive,
            singleton_skipped=False,
            warning=self._warnings.get(name),
            interval_s=interval_s,
            fire_at=fire_at,
            fired_at=fired_at,
        )

    def record_singleton_skip(
        self,
        name: str,
        *,
        interval_s: float | None = None,
        fire_at: float | None = None,
    ) -> None:
        """Record that this host skipped a singleton plugin (not the pinned host)."""
        prev = self._records.get(name)
        self._records[name] = PluginHealthRecord(
            name=name,
            kind=self.kind,
            outcome="skipped_singleton",
            last_run_at=prev.last_run_at if prev else None,
            last_success_at=prev.last_success_at if prev else None,
            last_failure_at=prev.last_failure_at if prev else None,
            last_error=None,
            consecutive_failures=prev.consecutive_failures if prev else 0,
            singleton_skipped=True,
            warning=self._warnings.get(name),
            interval_s=interval_s,
            fire_at=fire_at,
            fired_at=prev.fired_at if prev else None,
        )

    def record_config_skip(self, name: str) -> None:
        """Record that a plugin was skipped due to invalid singleton config."""
        now = time.time()
        prev = self._records.get(name)
        self._records[name] = PluginHealthRecord(
            name=name,
            kind=self.kind,
            outcome="skipped_config",
            last_run_at=now,
            last_success_at=prev.last_success_at if prev else None,
            last_failure_at=prev.last_failure_at if prev else None,
            last_error="invalid singleton config",
            consecutive_failures=prev.consecutive_failures if prev else 0,
            singleton_skipped=False,
            warning=self._warnings.get(name),
        )

    def record_timer_state(
        self,
        name: str,
        *,
        fire_at: float,
        fired_at: float | None,
        scheduled: bool,
    ) -> None:
        """Record a timer plugin's non-firing state (scheduled or already fired).

        Carries ``fire_at``/``fired_at`` so the board can render the timer
        schedule without a run having happened this tick.
        """
        prev = self._records.get(name)
        outcome: Outcome = "scheduled" if scheduled else "already_fired"
        self._records[name] = PluginHealthRecord(
            name=name,
            kind=self.kind,
            outcome=outcome,
            last_run_at=prev.last_run_at if prev else None,
            last_success_at=prev.last_success_at if prev else None,
            last_failure_at=prev.last_failure_at if prev else None,
            last_error=None,
            consecutive_failures=prev.consecutive_failures if prev else 0,
            singleton_skipped=False,
            warning=self._warnings.get(name),
            fire_at=fire_at,
            fired_at=fired_at,
        )

    def snapshot(self) -> list[PluginHealthRecord]:
        return list(self._records.values())

    def _signature(self) -> str:
        payload = json.dumps(
            [asdict(r) for r in self._records.values()],
            sort_keys=True,
            default=str,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    async def maybe_post(self, ctx: PollContext) -> None:
        """POST the snapshot when it changed, or as a heartbeat (~3 min)."""
        sig = self._signature()
        now = time.time()
        changed = sig != self._last_signature
        due_heartbeat = now - self._last_post_at >= _HEARTBEAT_S
        if not changed and not due_heartbeat:
            return
        body: dict[str, Any] = {"plugins": [asdict(r) for r in self._records.values()]}
        try:
            resp = await ctx.client.post(_HEALTH_PATH, json=body)
            if getattr(resp, "is_success", False):
                self._last_signature = sig
                self._last_post_at = now
            else:
                _logger.debug(
                    "plugin health POST %s -> %s",
                    _HEALTH_PATH,
                    getattr(resp, "status_code", "?"),
                )
        except Exception:  # noqa: BLE001 — health reporting must never break polling
            _logger.debug("plugin health POST failed", exc_info=True)
