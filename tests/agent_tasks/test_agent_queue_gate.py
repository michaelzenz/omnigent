"""Tests for the agent-queue dispatch gate."""

from __future__ import annotations

import pytest

from omnigent.agent_tasks.queue.gate import (
    ABANDON,
    DISPATCH,
    WAIT,
    DispatchGate,
)

_START = 1_000.0
_SESSION = "session-1"


@pytest.fixture
def gate() -> DispatchGate:
    return DispatchGate(started_at=_START, grace_period_s=3.0)


def test_idle_for_the_full_grace_period_dispatches(gate: DispatchGate) -> None:
    gate.observe(_SESSION, "idle", now=_START)

    assert gate.evaluate(_SESSION, now=_START + 3.0).action == DISPATCH


def test_idle_within_the_grace_period_waits(gate: DispatchGate) -> None:
    gate.observe(_SESSION, "idle", now=_START)

    decision = gate.evaluate(_SESSION, now=_START + 2.0)
    assert decision.action == WAIT
    assert decision.retry_after_s == pytest.approx(1.0)


def test_running_blocks_dispatch(gate: DispatchGate) -> None:
    gate.observe(_SESSION, "running", now=_START)

    assert gate.evaluate(_SESSION, now=_START + 60).action == WAIT


def test_waiting_blocks_dispatch(gate: DispatchGate) -> None:
    """``waiting`` means sub-agents are still running, so the turn is not over."""
    gate.observe(_SESSION, "waiting", now=_START)

    assert gate.evaluate(_SESSION, now=_START + 60).action == WAIT


def test_activity_resets_the_quiet_timer(gate: DispatchGate) -> None:
    """A user typing every couple of seconds must never be preempted."""
    gate.observe(_SESSION, "idle", now=_START)
    gate.observe(_SESSION, "running", now=_START + 2.0)
    gate.observe(_SESSION, "idle", now=_START + 2.5)

    # 3s have passed since the first idle, but only 0.5s of continuous quiet.
    assert gate.evaluate(_SESSION, now=_START + 3.0).action == WAIT
    assert gate.evaluate(_SESSION, now=_START + 5.5).action == DISPATCH


def test_repeated_idle_extends_rather_than_restarts_the_window(
    gate: DispatchGate,
) -> None:
    gate.observe(_SESSION, "idle", now=_START)
    gate.observe(_SESSION, "idle", now=_START + 1.0)
    gate.observe(_SESSION, "idle", now=_START + 2.0)

    assert gate.evaluate(_SESSION, now=_START + 3.0).action == DISPATCH


def test_failed_is_abandoned_rather_than_waited_on(gate: DispatchGate) -> None:
    """``failed`` is sticky, so waiting for idle would stall the queue forever."""
    gate.observe(_SESSION, "failed", now=_START)

    decision = gate.evaluate(_SESSION, now=_START + 3600)
    assert decision.action == ABANDON
    assert decision.reason == "target session failed"


def test_unobserved_session_does_not_fail_open(gate: DispatchGate) -> None:
    """A cold status cache reads as idle, so the gate must not trust it blindly."""
    assert gate.evaluate(_SESSION, now=_START + 1.0).action == WAIT
    assert gate.evaluate(_SESSION, now=_START + 3.0).action == DISPATCH


def test_grace_period_is_per_harness() -> None:
    gate = DispatchGate(
        started_at=_START,
        grace_period_s=3.0,
        grace_overrides={"claude-native": 10.0},
    )
    gate.observe(_SESSION, "idle", now=_START)

    assert gate.evaluate(_SESSION, now=_START + 5.0).action == DISPATCH
    assert gate.evaluate(_SESSION, now=_START + 5.0, harness="claude-native").action == WAIT
    assert gate.evaluate(_SESSION, now=_START + 10.0, harness="claude-native").action == DISPATCH


def test_forget_restarts_tracking(gate: DispatchGate) -> None:
    gate.observe(_SESSION, "running", now=_START)
    gate.forget(_SESSION)

    # Back to the unobserved path, which is gated from process start.
    assert gate.evaluate(_SESSION, now=_START + 3.0).action == DISPATCH


def test_tracking_is_bounded() -> None:
    """Session tracking must not grow without limit in a long-lived process."""
    from omnigent.agent_tasks.queue.gate import _MAX_TRACKED_SESSIONS

    gate = DispatchGate(started_at=_START)
    for index in range(_MAX_TRACKED_SESSIONS + 100):
        gate.observe(f"session-{index}", "running", now=_START + index)

    assert len(gate._sessions) <= _MAX_TRACKED_SESSIONS
    # The most recent session survived; the oldest were evicted.
    assert gate.evaluate(f"session-{_MAX_TRACKED_SESSIONS + 99}", now=_START).action == WAIT
