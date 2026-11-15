"""Tests for the GlobalHub watcher store."""

from __future__ import annotations

import uuid

from omnigent.stores.role_schedule_store.sqlalchemy_store import SqlAlchemyRoleScheduleStore
from omnigent.stores.watch_item_store import WatchObservation
from omnigent.stores.watch_item_store.sqlalchemy_store import SqlAlchemyWatchItemStore
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
SESSION_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
HOURLY = "FREQ=HOURLY;BYMINUTE=0"


def test_each_owner_lists_only_their_watchers_in_creation_order(db_uri: str) -> None:
    store = SqlAlchemyWatcherStore(db_uri)
    first = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    second = store.create(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        name="Ticket watcher",
        description="Tickets assigned to me",
        manual="Watch my tickets.",
        model="example-model",
    )
    bobs = store.create(uuid.uuid4().hex, owner_user_id=BOB, name="Bob's watcher")

    assert [watcher.id for watcher in store.list(owner_user_id=ALICE)] == [first.id, second.id]
    assert store.get(bobs.id, owner_user_id=ALICE) is None
    stored = store.get(second.id, owner_user_id=ALICE)
    assert stored is not None
    assert (stored.description, stored.manual, stored.model, stored.conversation_id) == (
        "Tickets assigned to me",
        "Watch my tickets.",
        "example-model",
        None,
    )
    assert (first.manual, first.model, first.role_key) == (None, None, f"watcher:{first.id}")


def test_an_update_changes_only_the_fields_it_names(db_uri: str) -> None:
    store = SqlAlchemyWatcherStore(db_uri)
    watcher = store.create(
        uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher", manual="Watch PRs."
    )

    named = store.update(watcher.id, owner_user_id=ALICE, conversation_id=SESSION_ID)
    reset = store.update(watcher.id, owner_user_id=ALICE, manual=None, name="Release watcher")
    unchanged = store.update(watcher.id, owner_user_id=ALICE)

    assert named is not None and named.conversation_id == SESSION_ID
    assert reset is not None
    assert (reset.name, reset.manual, reset.conversation_id) == (
        "Release watcher",
        None,
        SESSION_ID,
    )
    assert unchanged == reset
    assert store.update(watcher.id, owner_user_id=BOB, name="Mine") is None
    found = store.get_by_conversation_id(SESSION_ID, owner_user_id=ALICE)
    assert found is not None and found.id == watcher.id
    assert store.get_by_conversation_id(SESSION_ID, owner_user_id=BOB) is None


def test_delete_removes_the_watcher_and_only_its_own_schedule_and_items(db_uri: str) -> None:
    store = SqlAlchemyWatcherStore(db_uri)
    schedules = SqlAlchemyRoleScheduleStore(db_uri)
    items = SqlAlchemyWatchItemStore(db_uri)
    watcher = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    other = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="Ticket watcher")
    store.update(watcher.id, owner_user_id=ALICE, conversation_id=SESSION_ID)
    for role_key in (watcher.role_key, other.role_key, "custodian"):
        schedules.save(role_key, owner_user_id=ALICE, rrule=HOURLY, timezone="UTC", next_run_at=1)
    seen = [WatchObservation(source="github", source_key="https://example.com/1", offsets={})]
    for each in (watcher, other):
        items.observe(each, seen, session_id=None, now=1)

    refused = store.delete(watcher.id, owner_user_id=BOB)
    deleted = store.delete(watcher.id, owner_user_id=ALICE)

    assert refused is None
    assert deleted is not None and deleted.conversation_id == SESSION_ID
    assert store.get(watcher.id, owner_user_id=ALICE) is None
    assert store.delete(watcher.id, owner_user_id=ALICE) is None
    assert [
        schedules.get(role_key, owner_user_id=ALICE) is not None
        for role_key in (watcher.role_key, other.role_key, "custodian")
    ] == [False, True, True]
    counts = [
        items.count(each.id, owner_user_id=ALICE, statuses=("active", "done"))
        for each in (watcher, other)
    ]
    assert counts == [0, 1]
