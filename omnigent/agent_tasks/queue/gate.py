"""Dispatch gate — decide when an agent is quiet enough to receive an item.

An item may be handed over only when the target session has reported ``idle``
*continuously* for the grace period. The timer resets on any activity, so a user
typing every couple of seconds keeps the agent to themselves rather than being
preempted between keystrokes.

The gate is deliberately pure: it holds observed status and a clock reading, and
answers a question. All I/O — reading session status, sending the item — belongs
to the dispatcher.

Two observability quirks drive the shape of this module (both documented in the
session-status layer):

* ``waiting`` means "turn ended cleanly but sub-agents are still running", so it
  counts as busy, not idle.
* ``failed`` is sticky and is never downgraded by a trailing ``idle``. Treating
  it as "not idle, keep waiting" would stall that queue forever, so it resolves
  to :data:`ABANDON` and the dispatcher surfaces it as a halt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

# Continuous quiet required before dispatch. Load-bearing for native harnesses,
# where idle comes from a PTY-activity watcher with a trailing lag and can leak a
# brief idle between a turn ending and its queued continuation starting. Nearly
# decorative for SDK harnesses, which report a real turn lifecycle.
DEFAULT_GRACE_PERIOD_S = 3.0

# Statuses the session layer publishes.
QUIET_STATUS = "idle"
BUSY_STATUSES = frozenset({"running", "waiting"})
FAILED_STATUS = "failed"

DISPATCH: Literal["dispatch"] = "dispatch"
WAIT: Literal["wait"] = "wait"
ABANDON: Literal["abandon"] = "abandon"

# Cap on tracked sessions. Entries are evicted least-recently-seen first; losing
# one only costs a fresh grace window, never correctness.
_MAX_TRACKED_SESSIONS = 4096


@dataclass(frozen=True)
class GateDecision:
    """
    The gate's answer for one session.

    :param action: ``"dispatch"``, ``"wait"``, or ``"abandon"``.
    :param retry_after_s: How long to wait before asking again. Only meaningful
        for ``"wait"``.
    :param reason: Why the gate refused. ``None`` when dispatching.
    """

    action: Literal["dispatch", "wait", "abandon"]
    retry_after_s: float = 0.0
    reason: str | None = None


@dataclass
class _SessionQuiet:
    status: str
    quiet_since: float | None
    last_seen: float


class DispatchGate:
    """Tracks continuous quiet per session and rules on dispatch readiness."""

    def __init__(
        self,
        *,
        started_at: float,
        grace_period_s: float = DEFAULT_GRACE_PERIOD_S,
        grace_overrides: Mapping[str, float] | None = None,
    ) -> None:
        """
        :param started_at: Monotonic reading at process start. A session we have
            never observed is treated as quiet since this moment rather than
            quiet forever, so a restart does not make every queue instantly
            dispatchable while the status cache is still cold.
        :param grace_period_s: Default continuous-quiet requirement.
        :param grace_overrides: Per-harness overrides, keyed by harness name.
        """
        self._started_at = started_at
        self._grace_period_s = grace_period_s
        self._grace_overrides = dict(grace_overrides or {})
        self._sessions: dict[str, _SessionQuiet] = {}

    def grace_period_for(self, harness: str | None) -> float:
        """Return the continuous-quiet requirement for a harness."""
        if harness is None:
            return self._grace_period_s
        return self._grace_overrides.get(harness, self._grace_period_s)

    def observe(self, session_id: str, status: str, *, now: float) -> None:
        """Record a status reading, starting or resetting the quiet timer."""
        tracked = self._sessions.get(session_id)
        if status == QUIET_STATUS:
            # Keep the existing quiet_since: quiet must be *continuous*, so a
            # repeated idle reading extends the window rather than restarting it.
            quiet_since = (
                now if tracked is None or tracked.quiet_since is None else tracked.quiet_since
            )
        else:
            quiet_since = None
        if tracked is None:
            self._evict_if_full()
            self._sessions[session_id] = _SessionQuiet(
                status=status,
                quiet_since=quiet_since,
                last_seen=now,
            )
            return
        tracked.status = status
        tracked.quiet_since = quiet_since
        tracked.last_seen = now

    def observe_sync(self, session_id: str, status: str) -> None:
        """Synchronous adapter for :meth:`observe`, using ``time.monotonic``.

        Lets a status feed push readings into the gate without awaiting.
        """
        import time

        self.observe(session_id, status, now=time.monotonic())

    def evaluate(
        self,
        session_id: str,
        *,
        now: float,
        harness: str | None = None,
    ) -> GateDecision:
        """Decide whether an item may be handed to *session_id* right now."""
        grace = self.grace_period_for(harness)
        tracked = self._sessions.get(session_id)
        if tracked is None:
            # Never observed. Treat as quiet since process start so a cold status
            # cache reads as "wait a moment", not "everything is dispatchable".
            elapsed = now - self._started_at
            if elapsed >= grace:
                return GateDecision(action=DISPATCH)
            return GateDecision(
                action=WAIT,
                retry_after_s=grace - elapsed,
                reason="status not yet observed",
            )
        if tracked.status == FAILED_STATUS:
            return GateDecision(action=ABANDON, reason="target session failed")
        if tracked.quiet_since is None:
            return GateDecision(
                action=WAIT,
                retry_after_s=grace,
                reason=f"session is {tracked.status}",
            )
        elapsed = now - tracked.quiet_since
        if elapsed >= grace:
            return GateDecision(action=DISPATCH)
        return GateDecision(
            action=WAIT,
            retry_after_s=grace - elapsed,
            reason="within grace period",
        )

    def forget(self, session_id: str) -> None:
        """Drop tracking for a session, e.g. when its worker slot is rebound."""
        self._sessions.pop(session_id, None)

    def _evict_if_full(self) -> None:
        if len(self._sessions) < _MAX_TRACKED_SESSIONS:
            return
        oldest = min(self._sessions, key=lambda key: self._sessions[key].last_seen)
        del self._sessions[oldest]
