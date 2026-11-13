"""Tests for the GlobalHub task event store."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from omnigent.entities import EventTag
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
MANAGER = f"{100:032x}"
OTHER_MANAGER = f"{101:032x}"
# Creation order ties on the one-second created_at, then falls back to id.
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


def _record(store: SqlAlchemyTaskEventStore, *, owner: str | None = ALICE, **fields: Any) -> str:
    event_id = next(_ids)
    store.create(
        event_id,
        owner_user_id=owner,
        event_type=fields.pop("event_type", "github.pr.comment"),
        title=fields.pop("title", "Review comment"),
        **fields,
    )
    return event_id


def test_events_keep_their_details_for_their_owner(store: SqlAlchemyTaskEventStore) -> None:
    session_id = f"{200:032x}"
    unrouted_id = _record(
        store,
        payload={"url": "https://example.com/pull/1"},
        source="github",
        source_key="https://example.com/pull/1",
        source_offset="comment-1",
        source_session_id=session_id,
        tags=[EventTag(tag_type="repo", tag="example-org/storage")],
    )
    routed_id = _record(store, manager_id=MANAGER)

    unrouted = store.get(unrouted_id, owner_user_id=ALICE)
    routed = store.get(routed_id, owner_user_id=ALICE)

    assert unrouted is not None and routed is not None
    assert (unrouted.state, unrouted.payload, unrouted.tags, unrouted.source_session_id) == (
        "unrouted",
        {"url": "https://example.com/pull/1"},
        [EventTag(tag_type="repo", tag="example-org/storage")],
        session_id,
    )
    assert (routed.state, routed.manager_id, routed.payload, routed.tags) == (
        "routed",
        MANAGER,
        {},
        [],
    )
    assert routed.routed_at is not None and unrouted.routed_at is None
    assert store.get(unrouted_id, owner_user_id=BOB) is None


def test_a_repeated_report_from_a_source_is_found(store: SqlAlchemyTaskEventStore) -> None:
    first = _record(store, source="github", source_key="pull-1", source_offset="comment-1")
    _record(store, source="github", source_key="pull-1", source_offset="comment-1")
    _record(store, source="github", source_key="pull-1", source_offset="comment-2")
    without_offset = _record(store, source="github", source_key="pull-1")
    _record(store, owner=BOB, source="github", source_key="pull-2", source_offset="comment-1")

    def found(owner: str, event_type: str, offset: str | None) -> str | None:
        event = store.get_by_source(
            owner_user_id=owner,
            event_type=event_type,
            source="github",
            source_key="pull-1",
            source_offset=offset,
        )
        return event.id if event is not None else None

    assert found(ALICE, "github.pr.comment", "comment-1") == first
    assert found(ALICE, "github.pr.comment", None) == without_offset
    assert found(ALICE, "github.pr.review", "comment-1") is None
    assert found(BOB, "github.pr.comment", "comment-1") is None


def test_routing_to_a_manager_is_all_or_nothing(store: SqlAlchemyTaskEventStore) -> None:
    unrouted = _record(store)
    routed_here = _record(store, manager_id=MANAGER)
    routed_elsewhere = _record(store, manager_id=OTHER_MANAGER)
    dismissed = _record(store)
    store.dismiss(dismissed, owner_user_id=ALICE)
    bobs = _record(store, owner=BOB)

    for refused in (routed_elsewhere, dismissed, bobs, f"{999:032x}"):
        assert (
            store.route_to_manager([unrouted, refused], owner_user_id=ALICE, manager_id=MANAGER)
            is None
        )
    untouched = store.get(unrouted, owner_user_id=ALICE)
    assert untouched is not None and untouched.state == "unrouted"

    routed = store.route_to_manager(
        [unrouted, routed_here, unrouted], owner_user_id=ALICE, manager_id=MANAGER
    )

    assert routed is not None
    assert [(event.id, event.state, event.manager_id) for event in routed] == [
        (unrouted, "routed", MANAGER),
        (routed_here, "routed", MANAGER),
    ]
    assert routed[0].routed_at is not None


def test_dismiss_is_idempotent_and_owner_scoped(store: SqlAlchemyTaskEventStore) -> None:
    event_id = _record(store, manager_id=MANAGER)

    assert store.dismiss(event_id, owner_user_id=BOB) is None
    first = store.dismiss(event_id, owner_user_id=ALICE)
    again = store.dismiss(event_id, owner_user_id=ALICE)

    assert first is not None and again is not None
    assert (first.state, first.processed_at is not None) == ("dismissed", True)
    assert (again.state, again.processed_at) == ("dismissed", first.processed_at)


def test_lists_are_oldest_first_by_state_and_manager(store: SqlAlchemyTaskEventStore) -> None:
    first = _record(store)
    routed = _record(store, manager_id=MANAGER)
    second = _record(store)
    single_user = _record(store, owner=None)
    _record(store, owner=BOB)

    def ids(**filters: Any) -> list[str]:
        return [event.id for event in store.list(owner_user_id=ALICE, **filters)]

    assert ids(states=["unrouted"]) == [first, second]
    assert ids(manager_id=MANAGER) == [routed]
    assert ids(limit=2) == [first, routed]
    assert [event.id for event in store.list(owner_user_id=None)] == [single_user]
