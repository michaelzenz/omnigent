"""Tests for when GlobalHub role sessions get their scheduled runs (``role_schedules``)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from omnigent.agent_tasks.role_schedules import next_run_at, require_role_schedule
from omnigent.errors import OmnigentError


def _epoch(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=UTC).timestamp())


def test_a_daily_run_keeps_its_local_time_when_summer_time_ends() -> None:
    # Summer time in Los Angeles ends on 2026-11-01, so 09:00 moves from 16:00 to 17:00 UTC.
    rule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    created = _epoch(2026, 10, 1)

    saturday = next_run_at(
        rule, "America/Los_Angeles", after=_epoch(2026, 10, 31, 12), anchor=created
    )
    assert saturday is not None
    sunday = next_run_at(rule, "America/Los_Angeles", after=saturday, anchor=created)

    assert sunday is not None
    assert [datetime.fromtimestamp(when, tz=UTC) for when in (saturday, sunday)] == [
        datetime(2026, 10, 31, 16, tzinfo=UTC),
        datetime(2026, 11, 1, 17, tzinfo=UTC),
    ]


def test_an_interval_counts_from_the_day_the_schedule_was_created() -> None:
    # Every 5 hours from midnight on the creation day: 00, 05, 10, 15, 20, then 01 the next day.
    rule = "FREQ=HOURLY;INTERVAL=5;BYMINUTE=0"

    following = next_run_at(rule, "UTC", after=_epoch(2026, 11, 2, 21), anchor=_epoch(2026, 11, 2))

    assert following == _epoch(2026, 11, 3, 1)


@pytest.mark.parametrize(
    ("rule", "timezone", "reason"),
    [
        ("FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "Mars/Olympus_Mons", "invalid timezone"),
        ("FREQ=DAILY;BYHOUR=9;BYMINUTE=0;COUNT=3", "UTC", "remove COUNT and UNTIL"),
        ("FREQ=DAILY;UNTIL=20270101T000000Z", "UTC", "remove COUNT and UNTIL"),
        ("FREQ=MINUTELY;INTERVAL=5", "UTC", "Minimum interval is 10 minutes"),
        ("FREQ=SOMETIMES", "UTC", "Invalid RRULE"),
    ],
)
def test_a_schedule_must_repeat_without_an_end_at_least_ten_minutes_apart(
    rule: str, timezone: str, reason: str
) -> None:
    with pytest.raises(OmnigentError, match=reason):
        require_role_schedule(rule, timezone)


def test_a_ten_minute_interval_is_a_valid_schedule() -> None:
    require_role_schedule("FREQ=MINUTELY;INTERVAL=10", "America/Los_Angeles")
