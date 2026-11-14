"""Tests for the GlobalHub delivery queue store."""

from __future__ import annotations

import itertools

import pytest

from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
BROKER_SESSION = f"{300:032x}"
MANAGER_SESSION = f"{301:032x}"
NOW = 1_800_000_000
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def queue(db_uri: str) -> SqlAlchemyAgentQueueStore:
    return SqlAlchemyAgentQueueStore(db_uri)


@pytest.fixture()
def events(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


def _event(events: SqlAlchemyTaskEventStore, owner: str = ALICE, **fields: str) -> str:
    event_id = next(_ids)
    events.create(
        event_id, owner_user_id=owner, event_type="github.pr.comment", title="Comment", **fields
    )
    return event_id


def _enqueue(queue: SqlAlchemyAgentQueueStore, session_id: str, *event_ids: str) -> str:
    item = queue.enqueue(
        next(_ids),
        owner_user_id=ALICE,
        target_session_id=session_id,
        kind="events",
        event_ids=event_ids,
    )
    assert item is not None
    return item.id


def _item_of(events: SqlAlchemyTaskEventStore, event_id: str) -> str | None:
    event = events.get(event_id, owner_user_id=ALICE)
    assert event is not None
    return event.queue_item_id


def test_an_item_carries_its_events_all_or_none(
    queue: SqlAlchemyAgentQueueStore, events: SqlAlchemyTaskEventStore
) -> None:
    first, second, spare = _event(events), _event(events), _event(events)
    bobs = _event(events, owner=BOB)
    item_id = _enqueue(queue, BROKER_SESSION, first, second)

    refused = [
        queue.enqueue(
            next(_ids),
            owner_user_id=ALICE,
            target_session_id=BROKER_SESSION,
            kind="events",
            event_ids=[spare, taken],
        )
        for taken in (first, bobs, f"{999:032x}")
    ]
    click = queue.enqueue(
        next(_ids),
        owner_user_id=ALICE,
        target_session_id=BROKER_SESSION,
        kind="notice_click",
        payload={"action_id": "confirm_cleanup", "inputs": {"delete_a": True}},
    )

    assert [_item_of(events, event_id) for event_id in (first, second, spare)] == [
        item_id,
        item_id,
        None,
    ]
    assert refused == [None, None, None]
    assert click is not None
    assert (click.state, click.payload) == (
        "queued",
        {"action_id": "confirm_cleanup", "inputs": {"delete_a": True}},
    )
    assert queue.get(item_id, owner_user_id=BOB) is None


def test_each_session_gets_one_item_at_a_time_in_arrival_order(
    queue: SqlAlchemyAgentQueueStore,
) -> None:
    broker_first = _enqueue(queue, BROKER_SESSION)
    broker_second = _enqueue(queue, BROKER_SESSION)
    manager_item = _enqueue(queue, MANAGER_SESSION)

    def claim() -> list[str]:
        return [item.id for item in queue.claim_due(now=NOW)]

    claimed = claim()
    while_sending = claim()
    sent = queue.mark_sent(broker_first, owner_user_id=ALICE)
    while_turn_runs = claim()
    done = queue.mark_done(broker_first, owner_user_id=ALICE)

    assert claimed == [broker_first, manager_item]
    assert while_sending == []
    assert sent is not None and (sent.state, sent.sent_at is not None) == ("sent", True)
    assert while_turn_runs == []
    assert done is not None and done.state == "done"
    assert claim() == [broker_second]
    # Only a claimed item can be sent, and only its owner records it.
    assert queue.mark_sent(manager_item, owner_user_id=BOB) is None
    assert queue.mark_done(manager_item, owner_user_id=ALICE) is None


def test_a_failed_send_waits_for_its_retry_time_and_keeps_its_place(
    queue: SqlAlchemyAgentQueueStore,
) -> None:
    failing = _enqueue(queue, BROKER_SESSION)
    queue.claim_due(now=NOW)
    later = _enqueue(queue, BROKER_SESSION)

    retried = queue.retry_later(
        failing, owner_user_id=ALICE, error="host offline", not_before=NOW + 30
    )
    before_retry_time = queue.claim_due(now=NOW)
    at_retry_time = queue.claim_due(now=NOW + 30)

    assert retried is not None
    assert (retried.state, retried.attempts, retried.last_error, retried.not_before) == (
        "queued",
        1,
        "host offline",
        NOW + 30,
    )
    assert before_retry_time == []
    assert [item.id for item in at_retry_time] == [failing]
    assert [item.id for item in queue.list(owner_user_id=ALICE, states=["queued"])] == [later]


def test_deleting_an_item_releases_its_events_to_be_queued_again(
    queue: SqlAlchemyAgentQueueStore, events: SqlAlchemyTaskEventStore
) -> None:
    event_id = _event(events)
    item_id = _enqueue(queue, BROKER_SESSION, event_id)

    by_bob = queue.delete(item_id, owner_user_id=BOB)
    deleted = queue.delete(item_id, owner_user_id=ALICE)

    assert (by_bob, deleted) == (False, True)
    assert queue.get(item_id, owner_user_id=ALICE) is None
    assert _item_of(events, event_id) is None
    requeued = _enqueue(queue, BROKER_SESSION, event_id)
    assert _item_of(events, event_id) == requeued


def test_an_event_leaves_its_item_when_it_changes_hands(
    db_uri: str, queue: SqlAlchemyAgentQueueStore, events: SqlAlchemyTaskEventStore
) -> None:
    managers = SqlAlchemyManagerStore(db_uri)
    manager = managers.create(next(_ids), owner_user_id=ALICE, title="Storage", description="S3")
    unrouted = _event(events)
    _enqueue(queue, BROKER_SESSION, unrouted)

    events.route_to_manager([unrouted], owner_user_id=ALICE, manager_id=manager.id)
    routed_released = _item_of(events, unrouted)
    _enqueue(queue, MANAGER_SESSION, unrouted)
    managers.delete(manager.id, owner_user_id=ALICE)

    assert routed_released is None
    returned = events.get(unrouted, owner_user_id=ALICE)
    assert returned is not None
    assert (returned.state, returned.queue_item_id) == ("unrouted", None)
