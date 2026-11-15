"""When each user's GlobalHub role sessions get their scheduled run message."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.scheduled.rrule import (
    RRuleValidationError,
    get_next_fire_time,
    validate_rrule,
)

#: Parts that end a rule; a role schedule repeats until the user turns it off.
_ENDING_PARTS = frozenset({"COUNT", "UNTIL"})


def require_role_schedule(rrule: str, timezone: str) -> None:
    """
    Raise unless ``rrule`` can schedule a role session in ``timezone``.

    The rule must repeat without an end, with at least 10 minutes between runs,
    the minimum of main's continued scheduled tasks.

    :param rrule: An RFC 5545 recurrence rule, e.g. ``"FREQ=DAILY;BYHOUR=9;BYMINUTE=0"``.
    :param timezone: An IANA timezone name, e.g. ``"America/Los_Angeles"``.
    :raises OmnigentError: ``INVALID_INPUT`` for an unknown timezone, a rule
        with an end, or a rule that main's scheduler refuses.
    """
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise OmnigentError(
            f"invalid timezone {timezone!r}: must be a valid IANA timezone name",
            code=ErrorCode.INVALID_INPUT,
        ) from exc
    parts = {part.split("=", 1)[0].strip().upper() for part in rrule.split(";")}
    if parts & _ENDING_PARTS:
        raise OmnigentError(
            "a role schedule repeats until you turn it off: remove COUNT and UNTIL",
            code=ErrorCode.INVALID_INPUT,
        )
    try:
        validate_rrule(rrule, session_mode="continue")
    except RRuleValidationError as exc:
        raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc


def next_run_at(rrule: str, timezone: str, *, after: int, anchor: int) -> int | None:
    """
    Return the first run of ``rrule`` in ``timezone`` after ``after``.

    A daily time follows the local clock across summer time changes, as an
    automation's schedule does.

    :param rrule: A rule that :func:`require_role_schedule` accepts.
    :param timezone: An IANA timezone, e.g. ``"America/Los_Angeles"``.
    :param after: Unix epoch seconds.
    :param anchor: Unix epoch seconds when the schedule was created; intervals
        such as "every 5 hours" count from that day.
    :returns: Unix epoch seconds, or ``None`` when the rule has no later run.
    """
    fire = get_next_fire_time(
        rrule,
        datetime.fromtimestamp(after, tz=UTC),
        ZoneInfo(timezone),
        anchor=datetime.fromtimestamp(anchor, tz=UTC),
    )
    return int(fire.timestamp()) if fire is not None else None
