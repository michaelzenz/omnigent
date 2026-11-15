"""Tests for the GlobalHub role schedule store."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlRoleSchedule, workspace_scope
from omnigent.db.utils import get_or_create_engine
from omnigent.stores.role_schedule_store.sqlalchemy_store import SqlAlchemyRoleScheduleStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
CAROL = "carol@example.com"
DAILY = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
HOURLY = "FREQ=HOURLY;BYMINUTE=30"


def test_each_owner_has_one_schedule_per_role_that_a_save_replaces(db_uri: str) -> None:
    store = SqlAlchemyRoleScheduleStore(db_uri)
    first = store.save(
        "custodian", owner_user_id=ALICE, rrule=DAILY, timezone="UTC", next_run_at=1_000
    )
    other_role = store.save(
        "watcher:docs", owner_user_id=ALICE, rrule=HOURLY, timezone="UTC", next_run_at=1_800
    )
    store.save(
        "custodian", owner_user_id=BOB, rrule=DAILY, timezone="Asia/Tokyo", next_run_at=2_000
    )

    replaced = store.save(
        "custodian",
        owner_user_id=ALICE,
        rrule=HOURLY,
        timezone="Europe/Berlin",
        next_run_at=None,
    )

    stored = store.get("custodian", owner_user_id=ALICE)
    bobs = store.get("custodian", owner_user_id=BOB)
    assert stored is not None and bobs is not None
    assert (replaced.id, stored.id, stored.rrule, stored.timezone, stored.enabled) == (
        first.id,
        first.id,
        HOURLY,
        "Europe/Berlin",
        False,
    )
    assert other_role.id != first.id
    assert (bobs.rrule, bobs.timezone, bobs.next_run_at) == (DAILY, "Asia/Tokyo", 2_000)
    assert store.get("custodian", owner_user_id=CAROL) is None


def test_the_database_refuses_a_second_schedule_for_one_owner_and_role(db_uri: str) -> None:
    store = SqlAlchemyRoleScheduleStore(db_uri)
    store.save("custodian", owner_user_id=ALICE, rrule=DAILY, timezone="UTC", next_run_at=None)
    # What a concurrent save would insert after its check found no schedule.
    duplicate = SqlRoleSchedule(
        id=uuid.uuid4().hex,
        owner_user_id=ALICE,
        role_key="custodian",
        rrule=HOURLY,
        timezone="UTC",
        created_at=0,
    )

    with pytest.raises(IntegrityError), Session(get_or_create_engine(db_uri)) as session:
        session.add(duplicate)
        session.commit()

    stored = store.get("custodian", owner_user_id=ALICE)
    assert stored is not None and stored.rrule == DAILY


def test_a_delete_removes_only_the_owners_schedule_for_that_role(db_uri: str) -> None:
    store = SqlAlchemyRoleScheduleStore(db_uri)
    store.save("custodian", owner_user_id=ALICE, rrule=DAILY, timezone="UTC", next_run_at=1_000)
    store.save("watcher:docs", owner_user_id=ALICE, rrule=HOURLY, timezone="UTC", next_run_at=None)
    store.save("custodian", owner_user_id=BOB, rrule=DAILY, timezone="UTC", next_run_at=1_000)

    deleted = store.delete("custodian", owner_user_id=ALICE)
    again = store.delete("custodian", owner_user_id=ALICE)

    assert (deleted, again) == (True, False)
    assert store.get("custodian", owner_user_id=ALICE) is None
    assert store.get("watcher:docs", owner_user_id=ALICE) is not None
    assert store.get("custodian", owner_user_id=BOB) is not None


def test_the_due_scan_reads_every_workspace_and_one_pass_moves_a_schedule(db_uri: str) -> None:
    store = SqlAlchemyRoleScheduleStore(db_uri)
    store.save("custodian", owner_user_id=ALICE, rrule=DAILY, timezone="UTC", next_run_at=1_000)
    store.save("custodian", owner_user_id=BOB, rrule=DAILY, timezone="UTC", next_run_at=5_000)
    store.save("custodian", owner_user_id=CAROL, rrule=DAILY, timezone="UTC", next_run_at=None)
    with workspace_scope(7):
        other = store.save(
            "custodian", owner_user_id=ALICE, rrule=DAILY, timezone="UTC", next_run_at=900
        )

    due = store.list_due_all_workspaces(now=1_000)
    with workspace_scope(7):
        moved = store.advance(other.id, due_at=900, next_run_at=87_300)
        # Another pass that found the same due time moves nothing.
        again = store.advance(other.id, due_at=900, next_run_at=87_300)

    assert [(each.workspace_id, each.owner_user_id) for each in due] == [(7, ALICE), (0, ALICE)]
    assert (moved, again) == (True, False)
    assert [
        (each.workspace_id, each.owner_user_id)
        for each in store.list_due_all_workspaces(now=1_000)
    ] == [(0, ALICE)]


def test_advance_can_turn_a_schedule_off(db_uri: str) -> None:
    store = SqlAlchemyRoleScheduleStore(db_uri)
    schedule = store.save(
        "custodian", owner_user_id=ALICE, rrule=DAILY, timezone="UTC", next_run_at=1_000
    )

    assert store.advance(schedule.id, due_at=1_000, next_run_at=None) is True

    stored = store.get("custodian", owner_user_id=ALICE)
    assert stored is not None and stored.enabled is False
    assert store.list_due_all_workspaces(now=10_000) == []
