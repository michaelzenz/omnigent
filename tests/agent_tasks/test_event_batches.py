"""Tests for grouping GlobalHub task events into role session batches."""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from omnigent.agent_tasks.event_batches import BATCH_MAX_EVENTS, broker_batches, cluster_by_tags
from omnigent.entities import EventTag, TaskEvent

NOW = 1_800_000_000
FIRST_SESSION = f"{500:032x}"
SECOND_SESSION = f"{501:032x}"
_ids = (f"{n:032x}" for n in itertools.count(1))


def _event(
    *tags: str, age: int = 60, event_type: str = "github.pr.comment", session: str | None = None
) -> TaskEvent:
    return TaskEvent(
        id=next(_ids),
        owner_user_id="alice@example.com",
        event_type=event_type,
        title="Event",
        state="unrouted",
        created_at=NOW - age,
        source_session_id=session,
        tags=[EventTag(*tag.split(":", 1)) for tag in tags],
    )


def _ids_of(groups: Sequence[Sequence[TaskEvent]]) -> list[list[str]]:
    return [[event.id for event in group] for group in groups]


def test_events_cluster_by_tag_overlap_with_each_clusters_oldest_event() -> None:
    storage = _event("host:h1", "repo:storage", age=90)
    same_repo = _event("host:h1", "repo:storage", "pr:7", age=80)
    other_repo = _event("host:h1", "repo:web", age=70)
    host_only = _event("host:h1", age=60)
    untagged = _event(age=50)

    clusters = cluster_by_tags([untagged, other_repo, host_only, same_repo, storage])

    assert _ids_of(clusters) == [
        [storage.id, same_repo.id, host_only.id],
        [other_repo.id],
        [untagged.id],
    ]


def test_session_events_go_at_once_one_batch_per_session() -> None:
    first = _event(event_type="session.turn.finished", session=FIRST_SESSION, age=1)
    again = _event(event_type="session.turn.finished", session=FIRST_SESSION, age=0)
    other = _event(event_type="session.deleted", session=SECOND_SESSION, age=0)
    young_comment = _event("host:h1", age=1)

    batches = broker_batches([again, young_comment, other, first], now=NOW, broker_idle=True)

    assert _ids_of(batches) == [[first.id, again.id], [other.id]]


def test_other_events_wait_for_a_full_batch_or_an_old_batch_and_an_idle_broker() -> None:
    old = [_event("host:h1", "repo:storage", age=40) for _ in range(3)]
    young = [_event("host:h3", age=10)]
    many = [_event("host:h2", age=1) for _ in range(BATCH_MAX_EVENTS + 2)]
    events = [*many, *young, *old]

    idle = broker_batches(events, now=NOW, broker_idle=True)
    busy = broker_batches(events, now=NOW, broker_idle=False)

    assert _ids_of(idle) == _ids_of([old, many[:BATCH_MAX_EVENTS]])
    assert _ids_of(busy) == _ids_of([many[:BATCH_MAX_EVENTS]])
