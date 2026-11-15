"""Tests for the store of what each GlobalHub watcher tracks."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omnigent.entities import Watcher
from omnigent.errors import StaleCursorError
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.watch_item_store import (
    WATCH_CHANGED_EVENT_TYPE,
    WATCH_NEW_EVENT_TYPE,
    WatchChange,
    WatchItemLimitError,
    WatchObservation,
)
from omnigent.stores.watch_item_store.sqlalchemy_store import SqlAlchemyWatchItemStore
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
SESSION_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
PR = "https://github.com/example-org/repo/pull/12"
OTHER_PR = "https://github.com/example-org/repo/pull/13"


def _watcher(db_uri: str, owner: str = ALICE) -> Watcher:
    return SqlAlchemyWatcherStore(db_uri).create(
        uuid.uuid4().hex, owner_user_id=owner, name="PR watcher"
    )


def _seen(
    source_key: str = PR,
    *,
    offsets: dict[str, str | None] | None = None,
    title: str | None = None,
    task_id: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    announce: bool = False,
) -> WatchObservation:
    return WatchObservation(
        source="github",
        source_key=source_key,
        offsets=offsets if offsets is not None else {"state": "open", "checks": "pending@abc"},
        title=title,
        task_id=task_id,
        summary=summary,
        details=details,
        announce=announce,
    )


def test_a_first_look_is_silent_and_a_change_records_one_event(db_uri: str) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    events = SqlAlchemyTaskEventStore(db_uri)
    watcher = _watcher(db_uri)

    first = store.observe(watcher, [_seen(title="repo#12")], session_id=SESSION_ID, now=100)
    same = store.observe(watcher, [_seen()], session_id=SESSION_ID, now=200)
    changed = store.observe(
        watcher,
        [_seen(offsets={"checks": "failure@abc"}, details={"failing": ["unit-tests"]})],
        session_id=SESSION_ID,
        now=300,
    )

    assert [(result.outcome, result.event_id) for result in first + same] == [
        ("new", None),
        ("unchanged", None),
    ]
    (result,) = changed
    assert (result.outcome, result.changes) == (
        "changed",
        (WatchChange(offset="checks", before="pending@abc", after="failure@abc"),),
    )
    assert result.event_id is not None
    event = events.get(result.event_id, owner_user_id=ALICE)
    assert event is not None
    assert (event.event_type, event.state, event.manager_id) == (
        WATCH_CHANGED_EVENT_TYPE,
        "unrouted",
        None,
    )
    assert (event.title, event.source, event.source_key, event.source_offset) == (
        "repo#12: checks pending@abc -> failure@abc",
        "github",
        PR,
        "v1",
    )
    assert event.source_session_id == SESSION_ID
    assert [(tag.tag_type, tag.tag) for tag in event.tags] == [
        ("source", "github"),
        ("watcher", "PR watcher"),
    ]
    assert event.payload["details"] == {"failing": ["unit-tests"]}
    assert event.payload["offsets"] == {"state": "open", "checks": "failure@abc"}
    (item,) = store.list(watcher.id, owner_user_id=ALICE).data
    assert (item.title, item.version, item.checked_at, item.changed_at) == ("repo#12", 1, 300, 300)
    assert len(events.list(owner_user_id=ALICE)) == 1


def test_null_removes_an_offset_and_names_left_out_keep_their_value(db_uri: str) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    watcher = _watcher(db_uri)
    store.observe(watcher, [_seen()], session_id=None, now=100)

    (result,) = store.observe(
        watcher,
        [_seen(offsets={"checks": None, "review": "approved"}, summary="Approved")],
        session_id=None,
        now=200,
    )

    assert [change.offset for change in result.changes] == ["checks", "review"]
    (item,) = store.list(watcher.id, owner_user_id=ALICE).data
    assert item.offsets == {"state": "open", "review": "approved"}
    event = SqlAlchemyTaskEventStore(db_uri).get(result.event_id or "", owner_user_id=ALICE)
    assert event is not None and event.title == "Approved"


def test_an_announced_new_item_records_a_new_event(db_uri: str) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    watcher = _watcher(db_uri)

    (result,) = store.observe(
        watcher, [_seen(title="Review requested", announce=True)], session_id=None, now=100
    )

    event = SqlAlchemyTaskEventStore(db_uri).get(result.event_id or "", owner_user_id=ALICE)
    assert result.outcome == "new"
    assert event is not None
    assert (event.event_type, event.title, event.source_offset) == (
        WATCH_NEW_EVENT_TYPE,
        "New: Review requested",
        "new",
    )


def test_a_change_on_a_live_managed_task_goes_straight_to_its_manager(db_uri: str) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    tasks = SqlAlchemyTaskStore(db_uri)
    manager = SqlAlchemyManagerStore(db_uri).create(
        uuid.uuid4().hex, owner_user_id=ALICE, title="Storage"
    )
    live = tasks.create(
        uuid.uuid4().hex, owner_user_id=ALICE, title="S3 retries", goal="g", manager_id=manager.id
    )
    archived = tasks.create(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        title="Old work",
        goal="g",
        manager_id=manager.id,
        state="archived",
    )
    watcher = _watcher(db_uri)
    store.observe(
        watcher,
        [_seen(task_id=live.id), _seen(OTHER_PR, task_id=archived.id)],
        session_id=None,
        now=100,
    )

    results = store.observe(
        watcher,
        [_seen(offsets={"state": "merged"}), _seen(OTHER_PR, offsets={"state": "merged"})],
        session_id=None,
        now=200,
    )

    events = SqlAlchemyTaskEventStore(db_uri)
    routed, unrouted = (
        events.get(result.event_id or "", owner_user_id=ALICE) for result in results
    )
    assert routed is not None and unrouted is not None
    assert (routed.state, routed.manager_id, routed.task_id, routed.tags) == (
        "routed",
        manager.id,
        live.id,
        [],
    )
    assert (unrouted.state, unrouted.manager_id, unrouted.task_id) == (
        "unrouted",
        None,
        archived.id,
    )


def test_an_observation_that_would_keep_too_many_offsets_saves_nothing(db_uri: str) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    watcher = _watcher(db_uri)
    ten = {f"offset{n}": "x" for n in range(10)}
    store.observe(watcher, [_seen(offsets=ten)], session_id=None, now=100)

    with pytest.raises(WatchItemLimitError):
        store.observe(
            watcher,
            [_seen(OTHER_PR), _seen(offsets={"offset10": "y"})],
            session_id=None,
            now=200,
        )

    (item,) = store.list(watcher.id, owner_user_id=ALICE, statuses=("active", "done")).data
    assert (item.source_key, item.offsets, item.checked_at) == (PR, ten, 100)
    assert SqlAlchemyTaskEventStore(db_uri).list(owner_user_id=ALICE) == []


def test_pages_keep_the_tracking_order_and_a_purged_cursor_is_stale(db_uri: str) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    watcher = _watcher(db_uri)
    keys = [f"https://example.com/pr/{n}" for n in range(5)]
    for n, key in enumerate(keys):
        store.observe(watcher, [_seen(key)], session_id=None, now=100 + n)
    # A later check does not move an item to another page.
    store.observe(watcher, [_seen(keys[0], offsets={"state": "merged"})], session_id=None, now=200)

    first = store.list(watcher.id, owner_user_id=ALICE, limit=2)
    second = store.list(watcher.id, owner_user_id=ALICE, limit=2, after=first.last_id)
    third = store.list(watcher.id, owner_user_id=ALICE, limit=2, after=second.last_id)
    store.finish(watcher.id, [("github", keys[1])], owner_user_id=ALICE, now=300)
    store.purge_done(watcher.id, owner_user_id=ALICE, before=301)

    pages = [[item.source_key for item in page.data] for page in (first, second, third)]
    assert pages == [keys[:2], keys[2:4], keys[4:]]
    assert [page.has_more for page in (first, second, third)] == [True, True, False]
    assert (first.first_id, first.last_id) == (first.data[0].id, first.data[1].id)
    # The run message takes the least recently checked items instead.
    stalest = store.stalest(watcher.id, owner_user_id=ALICE, limit=2)
    assert [item.source_key for item in stalest] == keys[2:4]
    with pytest.raises(StaleCursorError):
        store.list(watcher.id, owner_user_id=ALICE, after=first.last_id)
    with pytest.raises(StaleCursorError):
        store.list(watcher.id, owner_user_id=ALICE, after="not-an-item-id")


def test_done_items_leave_the_active_list_until_observed_and_then_get_purged(
    db_uri: str,
) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    watcher = _watcher(db_uri)
    bobs = _watcher(db_uri, owner=BOB)
    store.observe(watcher, [_seen()], session_id=None, now=100)
    store.observe(watcher, [_seen(OTHER_PR)], session_id=None, now=101)
    store.observe(bobs, [_seen()], session_id=None, now=100)

    missing = store.finish(
        watcher.id,
        [("github", PR), ("github", "https://example.com/gone")],
        owner_user_id=ALICE,
        now=150,
    )
    active = store.list(watcher.id, owner_user_id=ALICE).data
    store.observe(watcher, [_seen()], session_id=None, now=200)
    reactivated = store.list(watcher.id, owner_user_id=ALICE).data
    store.finish(watcher.id, [("github", OTHER_PR)], owner_user_id=ALICE, now=300)
    kept = store.purge_done(watcher.id, owner_user_id=ALICE, before=300)
    purged = store.purge_done(watcher.id, owner_user_id=ALICE, before=301)

    assert missing == [("github", "https://example.com/gone")]
    assert [item.source_key for item in active] == [OTHER_PR]
    assert [(item.source_key, item.done_at) for item in reactivated] == [
        (PR, None),
        (OTHER_PR, None),
    ]
    assert (kept, purged) == (0, 1)
    assert [item.source_key for item in store.list(watcher.id, owner_user_id=ALICE).data] == [PR]
    assert store.count(watcher.id, owner_user_id=BOB) == 0
    assert store.count(bobs.id, owner_user_id=BOB) == 1
