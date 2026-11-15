"""When each user's custodian gets its daily session cleanup message."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.scheduled.rrule import get_next_fire_time


def require_sweep_timezone(timezone: str) -> None:
    """
    Raise unless ``timezone`` is an IANA timezone name.

    :raises OmnigentError: ``INVALID_INPUT`` for an unknown name.
    """
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise OmnigentError(
            f"invalid timezone {timezone!r}: must be a valid IANA timezone name",
            code=ErrorCode.INVALID_INPUT,
        ) from exc


def next_sweep_at(local_time: str, timezone: str, *, after: int) -> int:
    """
    Return the first time after ``after`` that ``local_time`` comes in ``timezone``.

    A day's time follows the local clock across summer time changes, as an
    automation's daily schedule does.

    :param local_time: A time of day, ``"HH:MM"``, e.g. ``"09:00"``.
    :param timezone: An IANA timezone, e.g. ``"America/Los_Angeles"``.
    :param after: Unix epoch seconds.
    :returns: Unix epoch seconds.
    """
    hour, minute = (int(part) for part in local_time.split(":"))
    fire = get_next_fire_time(
        f"FREQ=DAILY;BYHOUR={hour};BYMINUTE={minute}",
        datetime.fromtimestamp(after, tz=UTC),
        ZoneInfo(timezone),
    )
    # A daily rule without an end always has a next time.
    assert fire is not None
    return int(fire.timestamp())
