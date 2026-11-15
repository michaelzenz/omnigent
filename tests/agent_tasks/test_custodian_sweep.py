"""Tests for the next time of a user's daily session cleanup (``custodian_sweep``)."""

from __future__ import annotations

from datetime import UTC, datetime

from omnigent.agent_tasks.custodian_sweep import next_sweep_at


def test_the_next_sweep_keeps_its_local_time_when_summer_time_ends() -> None:
    # Summer time in Los Angeles ends on 2026-11-01, so 09:00 moves from 16:00 to 17:00 UTC.
    saturday_noon = int(datetime(2026, 10, 31, 12, tzinfo=UTC).timestamp())

    saturday = next_sweep_at("09:00", "America/Los_Angeles", after=saturday_noon)
    sunday = next_sweep_at("09:00", "America/Los_Angeles", after=saturday)

    assert [datetime.fromtimestamp(when, tz=UTC) for when in (saturday, sunday)] == [
        datetime(2026, 10, 31, 16, tzinfo=UTC),
        datetime(2026, 11, 1, 17, tzinfo=UTC),
    ]
