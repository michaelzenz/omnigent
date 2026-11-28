"""Tests for the store of what each GlobalHub watcher tracks."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omnigent.entities import Watcher
from omnigent.errors import StaleCursorError
from omnigent.stores.subscription_store.sqlalchemy_store import SqlAlchemySubscriptionStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.watch_item_store import (
    WATCH_CHANGED_EVENT_TYPE,
    WATCH_DONE_EVENT_TYPE,
    WATCH_NEW_EVENT_TYPE,
    WATCH_REOPENED_EVENT_TYPE,
    WatchChange,
    WatchFinish,
    WatchItemLimitError,
    WatchObservation,
)
from omnigent.stores.watch_item_store.sqlalchemy_store import SqlAlchemyWatchItemStore
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
SESSION_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
SOURCE_TYPE = "github.pull_request"
PR = "https://github.com/example-org/repo/pull/12"
OTHER_PR = "https://github.com/example-org/repo/pull/13"
THIRD_PR = "https://github.com/example-org/repo/pull/14"
UNTRACKED = "https://example.com/gone"
SLACK_THREAD = "slack.thread"
SLACK_CONVERSATION = "slack.conversation"
SLACK_DM = "D0000000001"
SLACK_THREAD_LINK = "https://example.com/archives/C0000000001/p1700000000000100"


def _watcher(db_uri: str, owner: str = ALICE) -> Watcher:
    return SqlAlchemyWatcherStore(db_uri).create(
        uuid.uuid4().hex, owner_user_id=owner, name="PR watcher"
    )


def _follow(
    db_uri: str, source_key: str, *, owner: str = ALICE, session_id: str | None = None
) -> str:
    follower = session_id or uuid.uuid4().hex
    SqlAlchemySubscriptionStore(db_uri).subscribe(
        owner_user_id=owner, session_id=follower, source_type=SOURCE_TYPE, source_key=source_key
    )
    return follower


def _seen(
    source_key: str = PR,
    *,
    offsets: dict[str, str | None] | None = None,
    title: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    announce: bool = False,
) -> WatchObservation:
    return WatchObservation(
        source=SOURCE_TYPE,
        source_key=source_key,
        offsets=offsets if offsets is not None else {"state": "open", "checks": "pending@abc"},
        title=title,
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

    assert [(result.outcome, result.event_ids) for result in first + same] == [
        ("new", ()),
        ("unchanged", ()),
    ]
    (result,) = changed
    assert (result.outcome, result.changes) == (
        "changed",
        (WatchChange(offset="checks", before="pending@abc", after="failure@abc"),),
    )
    (event_id,) = result.event_ids
    event = events.get(event_id, owner_user_id=ALICE)
    assert event is not None
    assert (event.event_type, event.state, event.target_session_id) == (
        WATCH_CHANGED_EVENT_TYPE,
        "unrouted",
        None,
    )
    assert (event.title, event.source, event.source_key, event.source_offset) == (
        "repo#12: checks pending@abc -> failure@abc",
        SOURCE_TYPE,
        PR,
        "v1",
    )
    assert event.source_session_id == SESSION_ID
    assert [(tag.tag_type, tag.tag) for tag in event.tags] == [
        ("source", SOURCE_TYPE),
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
    (event_id,) = result.event_ids
    event = SqlAlchemyTaskEventStore(db_uri).get(event_id, owner_user_id=ALICE)
    assert event is not None and event.title == "Approved"


def test_an_announced_new_item_records_a_new_event(db_uri: str) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    watcher = _watcher(db_uri)

    (result,) = store.observe(
        watcher, [_seen(title="Review requested", announce=True)], session_id=None, now=100
    )

    (event_id,) = result.event_ids
    event = SqlAlchemyTaskEventStore(db_uri).get(event_id, owner_user_id=ALICE)
    assert result.outcome == "new"
    assert event is not None
    assert (event.event_type, event.title, event.source_offset) == (
        WATCH_NEW_EVENT_TYPE,
        "New: Review requested",
        "new",
    )


def test_a_change_goes_to_each_following_session_but_the_reporter_else_to_the_broker(
    db_uri: str,
) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    events = SqlAlchemyTaskEventStore(db_uri)
    first, second = _follow(db_uri, PR), _follow(db_uri, PR)
    _follow(db_uri, PR, session_id=SESSION_ID)
    _follow(db_uri, OTHER_PR, session_id=SESSION_ID)
    _follow(db_uri, THIRD_PR, owner=BOB)
    watcher = _watcher(db_uri)
    keys = (PR, OTHER_PR, THIRD_PR)
    store.observe(watcher, [_seen(key) for key in keys], session_id=SESSION_ID, now=100)

    followed, followed_by_the_reporter, unfollowed = store.observe(
        watcher,
        [_seen(key, offsets={"state": "merged"}) for key in keys],
        session_id=SESSION_ID,
        now=200,
    )

    copies = [events.get(event_id, owner_user_id=ALICE) for event_id in followed.event_ids]
    assert len(copies) == 2
    assert {
        (event.target_session_id, event.state, event.routed_at, len(event.tags))
        for event in copies
        if event is not None
    } == {(first, "routed", 200, 0), (second, "routed", 200, 0)}
    assert (followed_by_the_reporter.outcome, followed_by_the_reporter.event_ids) == (
        "changed",
        (),
    )
    (broker_event_id,) = unfollowed.event_ids
    to_broker = events.get(broker_event_id, owner_user_id=ALICE)
    assert to_broker is not None
    assert (to_broker.target_session_id, to_broker.state, len(to_broker.tags)) == (
        None,
        "unrouted",
        2,
    )
    assert len(events.list(owner_user_id=ALICE)) == 3


def test_a_change_of_a_broker_routed_type_goes_only_to_the_broker_and_others_to_followers(
    db_uri: str,
) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    events = SqlAlchemyTaskEventStore(db_uri)
    SqlAlchemySubscriptionStore(db_uri).set_routing(
        owner_user_id=ALICE, source_type=SLACK_CONVERSATION, routing="broker"
    )
    _follow(db_uri, SLACK_DM)
    thread_follower = _follow(db_uri, SLACK_THREAD_LINK)
    watcher = _watcher(db_uri)
    sources = ((SLACK_CONVERSATION, SLACK_DM), (SLACK_THREAD, SLACK_THREAD_LINK))

    def seen(latest: str) -> list[WatchObservation]:
        return [
            WatchObservation(source=source, source_key=key, offsets={"latest": latest})
            for source, key in sources
        ]

    store.observe(watcher, seen("1"), session_id=SESSION_ID, now=100)
    conversation, thread = store.observe(watcher, seen("2"), session_id=SESSION_ID, now=200)
    store.finish(
        watcher,
        [WatchFinish(source, key) for source, key in sources],
        session_id=SESSION_ID,
        now=300,
    )

    def targets(event_ids: tuple[str, ...]) -> list[tuple[str | None, str]]:
        found = [events.get(event_id, owner_user_id=ALICE) for event_id in event_ids]
        return [(event.target_session_id, event.state) for event in found if event is not None]

    assert targets(conversation.event_ids) == [(None, "unrouted")]
    assert targets(thread.event_ids) == [(thread_follower, "routed")]
    assert [
        event.source_key
        for event in events.list(owner_user_id=ALICE)
        if event.event_type == WATCH_DONE_EVENT_TYPE
    ] == [SLACK_THREAD_LINK]


def test_quiet_items_are_the_active_ones_with_no_change_since_a_time(db_uri: str) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    watcher = _watcher(db_uri)
    store.observe(watcher, [_seen(PR), _seen(OTHER_PR), _seen(THIRD_PR)], session_id=None, now=100)
    store.finish(watcher, [WatchFinish(SOURCE_TYPE, THIRD_PR)], session_id=None, now=150)
    store.observe(
        watcher, [_seen(OTHER_PR, offsets={"state": "merged"})], session_id=None, now=300
    )
    store.observe(watcher, [_seen(PR)], session_id=None, now=400)

    quiet = store.list_quiet(watcher.id, owner_user_id=ALICE, before=200)

    assert [item.source_key for item in quiet] == [PR]
    assert store.list_quiet(watcher.id, owner_user_id=BOB, before=200) == []


def test_marking_items_done_tells_their_other_followers_once_and_never_the_broker(
    db_uri: str,
) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    events = SqlAlchemyTaskEventStore(db_uri)
    follower = _follow(db_uri, PR)
    _follow(db_uri, PR, session_id=SESSION_ID)
    other_follower = _follow(db_uri, OTHER_PR)
    watcher = _watcher(db_uri)
    store.observe(
        watcher,
        [_seen(title="repo#12"), _seen(OTHER_PR), _seen(THIRD_PR)],
        session_id=SESSION_ID,
        now=100,
    )

    missing = store.finish(
        watcher,
        [
            WatchFinish(SOURCE_TYPE, PR, summary=" Merged "),
            WatchFinish(SOURCE_TYPE, OTHER_PR),
            WatchFinish(SOURCE_TYPE, THIRD_PR),
            WatchFinish(SOURCE_TYPE, UNTRACKED),
        ],
        session_id=SESSION_ID,
        now=150,
    )
    finished_again = store.finish(
        watcher, [WatchFinish(SOURCE_TYPE, PR, summary="Closed")], session_id=SESSION_ID, now=160
    )

    assert (missing, finished_again) == ([(SOURCE_TYPE, UNTRACKED)], [])
    done_events = events.list(owner_user_id=ALICE)
    assert len(done_events) == 2
    assert {event.target_session_id: event.title for event in done_events} == {
        follower: "Done: repo#12: Merged",
        other_follower: f"Done: {OTHER_PR}",
    }
    assert {
        (event.event_type, event.state, event.source_offset, event.source_session_id)
        for event in done_events
    } == {(WATCH_DONE_EVENT_TYPE, "routed", "done-v0", SESSION_ID)}
    found = store.find(
        watcher.id,
        [
            (SOURCE_TYPE, PR),
            (SOURCE_TYPE, THIRD_PR),
            ("github.issue", PR),
            (SOURCE_TYPE, UNTRACKED),
        ],
        owner_user_id=ALICE,
    )
    assert sorted((item.source_key, item.status, item.done_at) for item in found) == [
        (PR, "done", 150),
        (THIRD_PR, "done", 150),
    ]
    assert store.find(watcher.id, [(SOURCE_TYPE, PR)], owner_user_id=BOB) == []
    assert store.find(watcher.id, [], owner_user_id=ALICE) == []


def test_a_done_item_stays_done_while_unchanged_and_reopens_when_its_offsets_change(
    db_uri: str,
) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    events = SqlAlchemyTaskEventStore(db_uri)
    watcher = _watcher(db_uri)
    store.observe(watcher, [_seen(title="repo#12")], session_id=None, now=100)
    store.finish(watcher, [WatchFinish(SOURCE_TYPE, PR)], session_id=None, now=150)
    follower = _follow(db_uri, PR)

    (unchanged,) = store.observe(watcher, [_seen()], session_id=None, now=200)
    (still_done,) = store.find(watcher.id, [(SOURCE_TYPE, PR)], owner_user_id=ALICE)
    (reopened,) = store.observe(
        watcher, [_seen(offsets={"checks": "success@abc"})], session_id=None, now=300
    )

    assert (unchanged.outcome, unchanged.event_ids) == ("unchanged", ())
    assert (still_done.status, still_done.done_at, still_done.checked_at) == ("done", 150, 200)
    assert (reopened.outcome, reopened.changes) == (
        "reopened",
        (WatchChange(offset="checks", before="pending@abc", after="success@abc"),),
    )
    (event_id,) = reopened.event_ids
    event = events.get(event_id, owner_user_id=ALICE)
    assert event is not None
    assert (event.event_type, event.target_session_id, event.title, event.source_offset) == (
        WATCH_REOPENED_EVENT_TYPE,
        follower,
        "repo#12: checks pending@abc -> success@abc",
        "v1",
    )
    assert [each.id for each in events.list(owner_user_id=ALICE)] == [event_id]
    (item,) = store.list(watcher.id, owner_user_id=ALICE).data
    assert (item.status, item.done_at, item.version, item.changed_at) == ("active", None, 1, 300)


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
    store.finish(watcher, [WatchFinish(SOURCE_TYPE, keys[1])], session_id=None, now=300)
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


def test_done_items_leave_the_active_list_and_the_old_ones_get_listed_and_purged(
    db_uri: str,
) -> None:
    store = SqlAlchemyWatchItemStore(db_uri)
    watcher = _watcher(db_uri)
    bobs = _watcher(db_uri, owner=BOB)
    store.observe(watcher, [_seen(), _seen(OTHER_PR), _seen(THIRD_PR)], session_id=None, now=100)
    store.observe(bobs, [_seen()], session_id=None, now=100)
    store.finish(watcher, [WatchFinish(SOURCE_TYPE, OTHER_PR)], session_id=None, now=120)
    store.finish(watcher, [WatchFinish(SOURCE_TYPE, PR)], session_id=None, now=150)
    store.finish(bobs, [WatchFinish(SOURCE_TYPE, PR)], session_id=None, now=110)

    active = store.list(watcher.id, owner_user_id=ALICE).data
    done_before_both = store.list_done_before(watcher.id, owner_user_id=ALICE, before=151)
    done_before_the_last = store.list_done_before(watcher.id, owner_user_id=ALICE, before=150)
    kept = store.purge_done(watcher.id, owner_user_id=ALICE, before=120)
    purged = store.purge_done(watcher.id, owner_user_id=ALICE, before=151)

    assert [item.source_key for item in active] == [THIRD_PR]
    assert [(item.source_key, item.done_at) for item in done_before_both] == [
        (OTHER_PR, 120),
        (PR, 150),
    ]
    assert [item.source_key for item in done_before_the_last] == [OTHER_PR]
    assert store.list_done_before(watcher.id, owner_user_id=BOB, before=151) == []
    assert (kept, purged) == (0, 2)
    remaining = store.list(watcher.id, owner_user_id=ALICE, statuses=("active", "done")).data
    assert [item.source_key for item in remaining] == [THIRD_PR]
    assert store.count(watcher.id, owner_user_id=BOB) == 0
    assert store.count(bobs.id, owner_user_id=BOB, statuses=("done",)) == 1
