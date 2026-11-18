"""The watchers that a new GlobalHub user starts with: GitHub, Slack and Jira, and the fallback.

The GitHub, Slack and Jira watchers are ordinary watchers, so the user can
change or delete them. Each one runs the packaged watcher template with its own
"What to watch" section. A user gets them once, with the first GlobalHub
placement; a deleted default watcher does not come back.

The fallback watcher is built in: every user has one, and it cannot be deleted.
It checks the sources that sessions follow when no other watcher covers their
type.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from omnigent.agent_tasks.role_schedules import DEFAULT_WATCHER_RRULE, save_role_schedule
from omnigent.agent_tasks.roles import WATCHER_ROLE, read_packaged_manual

if TYPE_CHECKING:
    from omnigent.entities import Watcher
    from omnigent.stores.role_schedule_store import RoleScheduleStore
    from omnigent.stores.user_settings_store import UserSettingsStore
    from omnigent.stores.watcher_store import WatcherStore

_WHAT_TO_WATCH_HEADING = "## What to watch"
FALLBACK_WATCHER_NAME = "Fallback watcher"
FALLBACK_WATCHER_DESCRIPTION = (
    "Checks the sources that sessions follow when no other watcher covers their type."
)
_FALLBACK_WATCHER_SECTION_FILE = "watcher_fallback.md"
_FALLBACK_WATCHER_ID_NAMESPACE = uuid.UUID("5f0c8f5e-3b7a-4d2e-9c61-7a2f4b8e1d90")


@dataclass(frozen=True)
class DefaultWatcher:
    """A watcher that a new GlobalHub user starts with.

    :param name: The watcher's name, e.g. ``"GitHub watcher"``.
    :param description: What the watcher watches.
    :param watch_file: Its packaged "What to watch" section, e.g. ``"watcher_github.md"``.
    :param rrule: When the watcher runs; minute intervals do not depend on the timezone.
    """

    name: str
    description: str
    watch_file: str
    rrule: str

    def manual(self) -> str:
        """Return the packaged watcher template with this watcher's "What to watch" section."""
        return _manual_with_section(self.watch_file)


def _manual_with_section(watch_file: str) -> str:
    """Return the packaged watcher template with a packaged "What to watch" section."""
    head, heading, _ = WATCHER_ROLE.default_manual().partition(_WHAT_TO_WATCH_HEADING)
    section = read_packaged_manual(watch_file).strip()
    return f"{head}{heading}\n\n{section}\n"


DEFAULT_WATCHERS = (
    DefaultWatcher(
        name="GitHub watcher",
        description=(
            "Review requests to me or my teams, comments and CI failures on my pull "
            "requests, and the pull requests that my tasks follow."
        ),
        watch_file="watcher_github.md",
        rrule=DEFAULT_WATCHER_RRULE,
    ),
    DefaultWatcher(
        name="Slack watcher",
        description="Direct messages, mentions of me, and new replies in the threads I follow.",
        watch_file="watcher_slack.md",
        rrule=DEFAULT_WATCHER_RRULE,
    ),
    DefaultWatcher(
        name="Jira watcher",
        description="New tickets assigned to me, and mentions of me.",
        watch_file="watcher_jira.md",
        rrule=DEFAULT_WATCHER_RRULE,
    ),
)


def add_default_watchers(
    watchers: WatcherStore,
    schedules: RoleScheduleStore,
    settings: UserSettingsStore,
    *,
    owner_user_id: str | None,
    now: int,
) -> list[Watcher]:
    """
    Create the default watchers and their schedules for a user who never got them. Blocking.

    Each watcher's session starts with its first run. The watchers share a
    creation second, so their ids ascend in the order of
    :data:`DEFAULT_WATCHERS`, which keeps that order in the watcher list.

    :param watchers: Where the watchers persist.
    :param schedules: Where their schedules persist.
    :param settings: Records that the user got the default watchers.
    :param owner_user_id: The new GlobalHub user, or ``None`` in single-user mode.
    :param now: Current Unix epoch seconds.
    :returns: The new watchers, or an empty list when the user got them before.
    """
    if settings.get_globalhub_default_watchers_added(user_id=owner_user_id):
        return []
    created = []
    watcher_ids = sorted(uuid.uuid4().hex for _ in DEFAULT_WATCHERS)
    for default, watcher_id in zip(DEFAULT_WATCHERS, watcher_ids, strict=True):
        watcher = watchers.create(
            watcher_id,
            owner_user_id=owner_user_id,
            name=default.name,
            description=default.description,
            manual=default.manual(),
        )
        save_role_schedule(
            schedules,
            watcher.role_key,
            owner_user_id=owner_user_id,
            rrule=default.rrule,
            timezone="UTC",
            enabled=True,
            now=now,
        )
        created.append(watcher)
    settings.save_globalhub_default_watchers_added(user_id=owner_user_id)
    return created


def packaged_watcher_manual(watcher: Watcher) -> str:
    """Return the manual a watcher runs without its own: the fallback manual or the template."""
    if watcher.builtin:
        return _manual_with_section(_FALLBACK_WATCHER_SECTION_FILE)
    return WATCHER_ROLE.default_manual()


def ensure_fallback_watcher(
    watchers: WatcherStore,
    schedules: RoleScheduleStore,
    *,
    owner_user_id: str | None,
    now: int,
) -> Watcher:
    """
    Return the user's built-in fallback watcher; create it and its schedule if needed. Blocking.

    Each owner's fallback watcher has one fixed id, so two concurrent calls
    cannot create two.

    :param watchers: Where the watchers persist.
    :param schedules: Where their schedules persist.
    :param owner_user_id: The owner, or ``None`` in single-user mode.
    :param now: Current Unix epoch seconds.
    :returns: The fallback watcher.
    """
    existing = watchers.get_builtin(owner_user_id=owner_user_id)
    if existing is not None:
        return existing
    try:
        watcher = watchers.create(
            uuid.uuid5(_FALLBACK_WATCHER_ID_NAMESPACE, owner_user_id or "").hex,
            owner_user_id=owner_user_id,
            name=FALLBACK_WATCHER_NAME,
            description=FALLBACK_WATCHER_DESCRIPTION,
            builtin=True,
        )
    except IntegrityError:
        created = watchers.get_builtin(owner_user_id=owner_user_id)
        if created is None:
            raise
        return created
    save_role_schedule(
        schedules,
        watcher.role_key,
        owner_user_id=owner_user_id,
        rrule=DEFAULT_WATCHER_RRULE,
        timezone="UTC",
        enabled=True,
        now=now,
    )
    return watcher
