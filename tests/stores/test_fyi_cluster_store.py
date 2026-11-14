"""Tests for the GlobalHub FYI cluster store."""

from __future__ import annotations

import itertools

import pytest

from omnigent.stores.fyi_cluster_store.sqlalchemy_store import SqlAlchemyFyiClusterStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
MANAGER = f"{100:032x}"
# Creation order ties on the one-second created_at, then falls back to id.
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def clusters(db_uri: str) -> SqlAlchemyFyiClusterStore:
    return SqlAlchemyFyiClusterStore(db_uri)


@pytest.fixture()
def events(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


def _event(
    events: SqlAlchemyTaskEventStore, *, owner: str = ALICE, manager_id: str | None = None
) -> str:
    event_id = next(_ids)
    events.create(
        event_id,
        owner_user_id=owner,
        event_type="slack.message",
        title="Channel chatter",
        manager_id=manager_id,
    )
    return event_id


def _state(events: SqlAlchemyTaskEventStore, event_id: str) -> tuple[str, str | None]:
    event = events.get(event_id, owner_user_id=ALICE)
    assert event is not None
    return event.state, event.fyi_cluster_id


def test_a_new_cluster_files_waiting_and_routed_events(
    clusters: SqlAlchemyFyiClusterStore, events: SqlAlchemyTaskEventStore
) -> None:
    unrouted, routed = _event(events), _event(events, manager_id=MANAGER)
    cluster_id = next(_ids)

    cluster = clusters.create(
        cluster_id,
        owner_user_id=ALICE,
        headline="Release chatter",
        event_ids=[unrouted, routed],
        rationale="No action needed",
    )

    assert cluster is not None
    assert (cluster.state, cluster.headline, cluster.rationale) == (
        "pending",
        "Release chatter",
        "No action needed",
    )
    filed = events.list(owner_user_id=ALICE, fyi_cluster_ids=[cluster_id])
    assert [(event.id, event.state) for event in filed] == [(unrouted, "fyi"), (routed, "fyi")]
    assert all(event.processed_at is not None for event in filed)
    assert [c.id for c in clusters.list(owner_user_id=ALICE, states=["pending"])] == [cluster_id]
    assert clusters.get(cluster_id, owner_user_id=BOB) is None


def test_filing_is_all_or_nothing(
    clusters: SqlAlchemyFyiClusterStore, events: SqlAlchemyTaskEventStore
) -> None:
    unrouted, dismissed = _event(events), _event(events)
    events.dismiss(dismissed, owner_user_id=ALICE)
    elsewhere = _event(events)
    other_cluster = next(_ids)
    clusters.create(other_cluster, owner_user_id=ALICE, headline="Other", event_ids=[elsewhere])
    bobs = _event(events, owner=BOB)

    for refused in (dismissed, elsewhere, bobs, f"{999:032x}"):
        cluster_id = next(_ids)
        assert (
            clusters.create(
                cluster_id,
                owner_user_id=ALICE,
                headline="Chatter",
                event_ids=[unrouted, refused],
            )
            is None
        )
        assert clusters.get(cluster_id, owner_user_id=ALICE) is None
    assert _state(events, unrouted) == ("unrouted", None)


def test_extending_a_pending_cluster_files_more_events_and_retitles_it(
    clusters: SqlAlchemyFyiClusterStore, events: SqlAlchemyTaskEventStore
) -> None:
    first, second = _event(events), _event(events)
    cluster_id = next(_ids)
    clusters.create(
        cluster_id,
        owner_user_id=ALICE,
        headline="Chatter",
        event_ids=[first],
        rationale="No action needed",
    )
    dismissed_cluster = next(_ids)
    clusters.create(dismissed_cluster, owner_user_id=ALICE, headline="Old", event_ids=[])
    clusters.dismiss(dismissed_cluster, owner_user_id=ALICE)

    extended = clusters.extend(
        cluster_id, owner_user_id=ALICE, headline="Release chatter", event_ids=[first, second]
    )

    assert extended is not None
    assert (extended.headline, extended.rationale) == ("Release chatter", "No action needed")
    assert _state(events, second) == ("fyi", cluster_id)
    for refused_cluster, owner in ((dismissed_cluster, ALICE), (cluster_id, BOB)):
        assert (
            clusters.extend(refused_cluster, owner_user_id=owner, headline="x", event_ids=[])
            is None
        )


def test_dismissing_a_cluster_dismisses_its_events_once(
    clusters: SqlAlchemyFyiClusterStore, events: SqlAlchemyTaskEventStore
) -> None:
    event_id = _event(events)
    cluster_id = next(_ids)
    clusters.create(cluster_id, owner_user_id=ALICE, headline="Chatter", event_ids=[event_id])

    assert clusters.dismiss(cluster_id, owner_user_id=BOB) is None
    first = clusters.dismiss(cluster_id, owner_user_id=ALICE)
    again = clusters.dismiss(cluster_id, owner_user_id=ALICE)

    assert first is not None and again is not None
    assert (first.state, first.resolved_at is not None) == ("dismissed", True)
    assert again.resolved_at == first.resolved_at
    assert _state(events, event_id) == ("dismissed", cluster_id)
    assert clusters.list(owner_user_id=ALICE, states=["pending"]) == []
