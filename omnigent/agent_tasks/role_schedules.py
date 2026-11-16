"""When each user's GlobalHub role sessions get their scheduled run message."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from omnigent.agent_tasks.roles import CUSTODIAN_ROLE_KEY
from omnigent.entities import RoleSchedule
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.scheduled.rrule import (
    RRuleValidationError,
    get_next_fire_time,
    validate_rrule,
)
from omnigent.stores.role_schedule_store import RoleScheduleStore

#: How often a new watcher runs unless the user picks another schedule.
DEFAULT_WATCHER_RRULE = "FREQ=MINUTELY;INTERVAL=30"
#: When a new GlobalHub user's custodian sweeps sessions unless the user picks another time.
DEFAULT_CUSTODIAN_RRULE = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
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


def save_role_schedule(
    schedules: RoleScheduleStore,
    role_key: str,
    *,
    owner_user_id: str | None,
    rrule: str,
    timezone: str,
    enabled: bool,
    now: int,
) -> RoleSchedule:
    """
    Save the owner's schedule for a role, and its next run after ``now`` while on. Blocking.

    A schedule that already exists keeps the day its intervals count from.

    :param schedules: Where the schedules persist.
    :param role_key: The role that runs, e.g. ``"custodian"`` or ``"watcher:<id>"``.
    :param owner_user_id: The owner, or ``None`` in single-user mode.
    :param rrule: A rule that :func:`require_role_schedule` accepts.
    :param timezone: An IANA timezone, e.g. ``"America/Los_Angeles"``.
    :param enabled: Whether the role gets runs; ``False`` keeps the rule only.
    :param now: Current Unix epoch seconds.
    :returns: The saved schedule.
    """
    saved = schedules.get(role_key, owner_user_id=owner_user_id)
    anchor = saved.created_at if saved is not None else now
    upcoming = next_run_at(rrule, timezone, after=now, anchor=anchor) if enabled else None
    return schedules.save(
        role_key,
        owner_user_id=owner_user_id,
        rrule=rrule,
        timezone=timezone,
        next_run_at=upcoming,
    )


def start_default_custodian_schedule(
    schedules: RoleScheduleStore, *, owner_user_id: str | None, timezone: str, now: int
) -> RoleSchedule | None:
    """
    Turn on the custodian's daily sweep for a user with no custodian schedule. Blocking.

    :param schedules: Where the schedules persist.
    :param owner_user_id: The new GlobalHub user, or ``None`` in single-user mode.
    :param timezone: A valid IANA timezone of the user, e.g. ``"America/Los_Angeles"``.
    :param now: Current Unix epoch seconds.
    :returns: The new schedule, or ``None`` when the user has one already, on or off.
    """
    if schedules.get(CUSTODIAN_ROLE_KEY, owner_user_id=owner_user_id) is not None:
        return None
    return save_role_schedule(
        schedules,
        CUSTODIAN_ROLE_KEY,
        owner_user_id=owner_user_id,
        rrule=DEFAULT_CUSTODIAN_RRULE,
        timezone=timezone,
        enabled=True,
        now=now,
    )
