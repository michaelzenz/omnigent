"""Tests for the watchers that a new GlobalHub user starts with."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.default_watchers import (
    DEFAULT_WATCHERS,
    FALLBACK_WATCHER_DESCRIPTION,
    FALLBACK_WATCHER_NAME,
    DefaultWatcher,
    ensure_fallback_watcher,
    packaged_watcher_manual,
)
from omnigent.agent_tasks.role_schedules import DEFAULT_WATCHER_RRULE, require_role_schedule
from omnigent.agent_tasks.roles import WATCHER_ROLE
from omnigent.entities import Watcher
from omnigent.stores.role_schedule_store.sqlalchemy_store import SqlAlchemyRoleScheduleStore
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
NOW = 1_800_000_000


@pytest.mark.parametrize("default", DEFAULT_WATCHERS, ids=lambda default: default.name)
def test_a_default_watcher_runs_the_template_with_its_own_watch_section(
    default: DefaultWatcher,
) -> None:
    template_head = WATCHER_ROLE.default_manual().partition("## What to watch")[0]

    watch_section = default.manual().removeprefix(template_head)

    assert watch_section.startswith("## What to watch\n\n")
    assert "Replace this example" not in watch_section
    assert "Check the items that you already track" in watch_section
    assert (
        watch_section.rstrip().rpartition("\n\n")[2].startswith("If you do not have access to the")
    )
    require_role_schedule(default.rrule, "UTC")


def test_each_owner_gets_one_scheduled_fallback_watcher(db_uri: str) -> None:
    watchers = SqlAlchemyWatcherStore(db_uri)
    schedules = SqlAlchemyRoleScheduleStore(db_uri)

    def ensure(owner: str | None) -> Watcher:
        return ensure_fallback_watcher(watchers, schedules, owner_user_id=owner, now=NOW)

    first = ensure(ALICE)
    again = ensure(ALICE)
    bobs, single_user = ensure(BOB), ensure(None)

    assert (first.name, first.description, first.manual, first.builtin) == (
        FALLBACK_WATCHER_NAME,
        FALLBACK_WATCHER_DESCRIPTION,
        None,
        True,
    )
    assert again == first
    assert len({first.id, bobs.id, single_user.id}) == 3
    assert [watcher.id for watcher in watchers.list(owner_user_id=ALICE)] == [first.id]
    schedule = schedules.get(first.role_key, owner_user_id=ALICE)
    assert schedule is not None
    assert (schedule.rrule, schedule.timezone, schedule.enabled) == (
        DEFAULT_WATCHER_RRULE,
        "UTC",
        True,
    )


def test_a_racing_call_returns_the_fallback_watcher_that_the_first_call_created(
    db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    watchers = SqlAlchemyWatcherStore(db_uri)
    schedules = SqlAlchemyRoleScheduleStore(db_uri)
    created = ensure_fallback_watcher(watchers, schedules, owner_user_id=ALICE, now=NOW)
    read_builtin = watchers.get_builtin
    stale_reads: list[Watcher | None] = [None]

    def racing_get_builtin(*, owner_user_id: str | None) -> Watcher | None:
        return stale_reads.pop() if stale_reads else read_builtin(owner_user_id=owner_user_id)

    monkeypatch.setattr(watchers, "get_builtin", racing_get_builtin)

    raced = ensure_fallback_watcher(watchers, schedules, owner_user_id=ALICE, now=NOW + 60)

    assert raced == created
    assert [watcher.id for watcher in watchers.list(owner_user_id=ALICE)] == [created.id]


def test_only_the_fallback_watcher_runs_the_fallback_watch_section() -> None:
    def watcher(*, builtin: bool) -> Watcher:
        return Watcher(
            id=uuid.uuid4().hex,
            owner_user_id=ALICE,
            name="Watcher",
            description="",
            created_at=NOW,
            updated_at=NOW,
            builtin=builtin,
        )

    template = WATCHER_ROLE.default_manual()
    template_head = template.partition("## What to watch")[0]

    fallback_section = packaged_watcher_manual(watcher(builtin=True)).removeprefix(template_head)

    assert fallback_section.startswith(
        "## What to watch\n\nYou are the built-in fallback watcher."
    )
    assert "[Followed sources]" in fallback_section
    assert packaged_watcher_manual(watcher(builtin=False)) == template
