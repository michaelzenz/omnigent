"""Tests for the host ambient poll scheduler."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from omnigent.host.polling.context import PollContext
from omnigent.host.polling.scheduler import PollScheduler


@dataclass
class _FakePoller:
    name: str
    enabled_flag: bool = True
    interval: float = 0.01
    poll_count: int = 0
    start_count: int = 0
    stop_count: int = 0
    fail_times: int = 0
    poll_delay_s: float = 0.0
    _failures_seen: int = field(default=0, init=False)

    def enabled(self, ctx: PollContext) -> bool:
        return self.enabled_flag

    def interval_s(self, ctx: PollContext) -> float:
        return self.interval

    async def on_start(self, ctx: PollContext) -> None:
        self.start_count += 1

    async def poll_once(self, ctx: PollContext) -> None:
        if self.poll_delay_s:
            await asyncio.sleep(self.poll_delay_s)
        if self._failures_seen < self.fail_times:
            self._failures_seen += 1
            raise RuntimeError("poll failed")
        self.poll_count += 1

    async def on_stop(self) -> None:
        self.stop_count += 1


@pytest.mark.asyncio
async def test_poll_scheduler_starts_and_stops_enabled_poller() -> None:
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        await asyncio.sleep(0)

    poller = _FakePoller(name="demo")
    scheduler = PollScheduler(
        server_url="http://test",
        host_id="a" * 32,
        sleep=_sleep,
    )
    scheduler.register(poller)

    await scheduler.start()
    assert scheduler.is_started
    await asyncio.sleep(0)
    await scheduler.stop()

    assert poller.start_count == 1
    assert poller.poll_count >= 1
    assert poller.stop_count == 1
    assert scheduler.stats()["demo"].last_ok_at is not None


@pytest.mark.asyncio
async def test_poll_scheduler_skips_disabled_poller() -> None:
    poller = _FakePoller(name="demo", enabled_flag=False)
    scheduler = PollScheduler(server_url="http://test", host_id="a" * 32)
    scheduler.register(poller)

    await scheduler.start()
    await asyncio.sleep(0)
    await scheduler.stop()

    assert poller.start_count == 0
    assert poller.poll_count == 0


@pytest.mark.asyncio
async def test_poll_scheduler_applies_exponential_backoff() -> None:
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        await asyncio.sleep(0)

    poller = _FakePoller(name="demo", fail_times=2, interval=0.05)
    scheduler = PollScheduler(
        server_url="http://test",
        host_id="a" * 32,
        sleep=_sleep,
        backoff_cap_s=0.2,
    )
    scheduler.register(poller)

    await scheduler.start()
    await asyncio.sleep(0.15)
    await scheduler.stop()

    assert any(delay > poller.interval for delay in sleeps)


@pytest.mark.asyncio
async def test_poll_scheduler_counts_skipped_overlaps() -> None:
    poller = _FakePoller(name="demo", poll_delay_s=0.05, interval=0.001)
    scheduler = PollScheduler(server_url="http://test", host_id="a" * 32)
    scheduler.register(poller)

    await scheduler.start()
    await asyncio.sleep(0.02)
    await scheduler.stop()

    assert scheduler.stats()["demo"].skipped_overlaps >= 1
