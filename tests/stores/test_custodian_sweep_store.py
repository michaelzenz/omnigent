"""Tests for the GlobalHub custodian sweep store."""

from __future__ import annotations

from omnigent.db.db_models import workspace_scope
from omnigent.stores.custodian_sweep_store.sqlalchemy_store import SqlAlchemyCustodianSweepStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
CAROL = "carol@example.com"


def test_each_owner_has_one_sweep_that_a_save_replaces(db_uri: str) -> None:
    store = SqlAlchemyCustodianSweepStore(db_uri)
    first = store.save(owner_user_id=ALICE, local_time="09:00", timezone="UTC", next_run_at=1_000)
    store.save(owner_user_id=BOB, local_time="07:30", timezone="Asia/Tokyo", next_run_at=2_000)

    replaced = store.save(
        owner_user_id=ALICE, local_time="18:30", timezone="Europe/Berlin", next_run_at=None
    )

    stored, bobs = store.get(owner_user_id=ALICE), store.get(owner_user_id=BOB)
    assert stored is not None and bobs is not None
    assert (replaced.id, stored.id, stored.local_time, stored.timezone, stored.enabled) == (
        first.id,
        first.id,
        "18:30",
        "Europe/Berlin",
        False,
    )
    assert (bobs.local_time, bobs.timezone, bobs.next_run_at) == ("07:30", "Asia/Tokyo", 2_000)
    assert store.get(owner_user_id=CAROL) is None


def test_the_due_scan_reads_every_workspace_and_one_pass_moves_a_sweep(db_uri: str) -> None:
    store = SqlAlchemyCustodianSweepStore(db_uri)
    store.save(owner_user_id=ALICE, local_time="09:00", timezone="UTC", next_run_at=1_000)
    store.save(owner_user_id=BOB, local_time="09:00", timezone="UTC", next_run_at=5_000)
    store.save(owner_user_id=CAROL, local_time="09:00", timezone="UTC", next_run_at=None)
    with workspace_scope(7):
        other = store.save(
            owner_user_id=ALICE, local_time="09:00", timezone="UTC", next_run_at=900
        )

    due = store.list_due_all_workspaces(now=1_000)
    with workspace_scope(7):
        moved = store.advance(other.id, due_at=900, next_run_at=87_300)
        # Another pass that found the same due time moves nothing.
        again = store.advance(other.id, due_at=900, next_run_at=87_300)

    assert [(sweep.workspace_id, sweep.owner_user_id) for sweep in due] == [(7, ALICE), (0, ALICE)]
    assert (moved, again) == (True, False)
    assert [
        (sweep.workspace_id, sweep.owner_user_id)
        for sweep in store.list_due_all_workspaces(now=1_000)
    ] == [(0, ALICE)]
