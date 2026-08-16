"""Tests for the host-side PluginHealthTracker."""

from __future__ import annotations

import asyncio

import httpx

from omnigent.host.polling.context import PollContext
from omnigent.host.polling.plugin_health import PluginHealthTracker


def _ctx(monkeypatch, responder) -> PollContext:
    async def post(self, url, json=None):
        return responder()

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return PollContext(
        server_url="http://test",
        host_id="host-A",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )


def test_record_run_ok_clears_failures() -> None:
    t = PluginHealthTracker(kind="poll")
    t.record_run("p", outcome="exit_nonzero", error="boom")
    t.record_run("p", outcome="ok")
    snap = t.snapshot()[0]
    assert snap.consecutive_failures == 0
    assert snap.last_error is None
    assert snap.last_success_at is not None


def test_record_run_failure_increments_consecutive() -> None:
    t = PluginHealthTracker(kind="poll")
    t.record_run("p", outcome="exit_nonzero", error="boom")
    t.record_run("p", outcome="timeout", error="slow")
    snap = t.snapshot()[0]
    assert snap.consecutive_failures == 2
    assert snap.last_failure_at is not None
    assert snap.last_error == "slow"


def test_singleton_skip_records_without_failure() -> None:
    t = PluginHealthTracker(kind="poll")
    t.record_singleton_skip("p", interval_s=60.0)
    snap = t.snapshot()[0]
    assert snap.singleton_skipped is True
    assert snap.outcome == "skipped_singleton"
    assert snap.consecutive_failures == 0
    assert snap.interval_s == 60.0


def test_set_warnings_carries_into_records_and_clears() -> None:
    t = PluginHealthTracker(kind="poll")
    t.set_warnings({"dup"}, "duplicate plugin name")
    t.record_run("dup", outcome="ok")
    t.record_run("clean", outcome="ok")
    by_name = {r.name: r for r in t.snapshot()}
    assert by_name["dup"].warning == "duplicate plugin name"
    assert by_name["clean"].warning is None
    # Resolving the collision clears the warning on the next scan, even for
    # plugins that don't run again this tick (existing records are updated).
    t.set_warnings(set(), "duplicate plugin name")
    by_name = {r.name: r for r in t.snapshot()}
    assert by_name["dup"].warning is None
    assert by_name["clean"].warning is None


def test_set_warnings_applies_to_existing_records() -> None:
    t = PluginHealthTracker(kind="poll")
    t.record_run("dup", outcome="ok")
    # A collision detected after the run should still mark the existing record.
    t.set_warnings({"dup"}, "duplicate plugin name")
    assert t.snapshot()[0].warning == "duplicate plugin name"


def test_timer_state_records_scheduled_then_fired() -> None:
    t = PluginHealthTracker(kind="timer")
    t.record_timer_state("r", fire_at=100.0, fired_at=None, scheduled=True)
    assert t.snapshot()[0].outcome == "scheduled"
    t.record_timer_state("r", fire_at=100.0, fired_at=100.0, scheduled=False)
    assert t.snapshot()[0].outcome == "already_fired"
    assert t.snapshot()[0].fired_at == 100.0


def test_error_truncated() -> None:
    t = PluginHealthTracker(kind="poll")
    t.record_run("p", outcome="exit_nonzero", error="x" * 1000)
    snap = t.snapshot()[0]
    assert len(snap.last_error) <= 501  # 500 + ellipsis
    assert snap.last_error.endswith("…")


def test_maybe_post_on_change_then_heartbeat(monkeypatch) -> None:
    t = PluginHealthTracker(kind="poll")
    posts = []

    def responder():
        posts.append(1)
        return httpx.Response(200)

    ctx = _ctx(monkeypatch, responder)
    t.record_run("p", outcome="ok")
    asyncio.run(t.maybe_post(ctx))
    assert len(posts) == 1
    # No change -> not posted again immediately.
    asyncio.run(t.maybe_post(ctx))
    assert len(posts) == 1
    # Change -> posted.
    t.record_run("p", outcome="exit_nonzero", error="boom")
    asyncio.run(t.maybe_post(ctx))
    assert len(posts) == 2
