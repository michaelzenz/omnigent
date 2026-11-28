"""Tests for the GlobalHub watcher store."""

from __future__ import annotations

import uuid

import pytest

from omnigent.entities import Watcher, WatcherProblem, WatcherProblemSource
from omnigent.stores.role_schedule_store.sqlalchemy_store import SqlAlchemyRoleScheduleStore
from omnigent.stores.subscription_store.sqlalchemy_store import SqlAlchemySubscriptionStore
from omnigent.stores.watch_item_store import WatchObservation
from omnigent.stores.watch_item_store.sqlalchemy_store import SqlAlchemyWatchItemStore
from omnigent.stores.watcher_store import sqlalchemy_store as watcher_sqlalchemy
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
SESSION_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
HOURLY = "FREQ=HOURLY;BYMINUTE=0"


def test_each_owner_lists_only_their_watchers_in_creation_order(
    db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Watchers made in the same second list by id, so each create gets its own second.
    clock = iter([100, 200, 300])
    monkeypatch.setattr(watcher_sqlalchemy, "now_epoch", lambda: next(clock))
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


def test_each_owner_finds_only_their_own_builtin_watcher(db_uri: str) -> None:
    store = SqlAlchemyWatcherStore(db_uri)
    ordinary = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    before = store.get_builtin(owner_user_id=ALICE)
    fallback = store.create(
        uuid.uuid4().hex, owner_user_id=ALICE, name="Fallback watcher", builtin=True
    )

    found = store.get_builtin(owner_user_id=ALICE)

    assert before is None
    assert (ordinary.builtin, fallback.builtin) == (False, True)
    assert found == fallback
    stored = store.get(ordinary.id, owner_user_id=ALICE)
    assert stored is not None and stored.builtin is False
    assert store.get_builtin(owner_user_id=BOB) is None
    assert store.get_builtin(owner_user_id=None) is None


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


def test_a_cursor_stays_until_the_watcher_replaces_or_clears_it(db_uri: str) -> None:
    store = SqlAlchemyWatcherStore(db_uri)
    watcher = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="Slack watcher")

    saved = store.set_cursor(watcher.id, "1700000000.000100", owner_user_id=ALICE)
    by_bob = store.set_cursor(watcher.id, "mine", owner_user_id=BOB)
    stored = store.get(watcher.id, owner_user_id=ALICE)
    replaced = store.set_cursor(watcher.id, "1700000600.000200", owner_user_id=ALICE)
    cleared = store.set_cursor(watcher.id, None, owner_user_id=ALICE)

    assert watcher.cursor is None
    assert saved is not None and saved.cursor == "1700000000.000100"
    assert by_bob is None
    assert stored is not None and stored.cursor == "1700000000.000100"
    assert replaced is not None and replaced.cursor == "1700000600.000200"
    assert cleared is not None and cleared.cursor is None


def test_delete_removes_the_watcher_and_only_its_own_schedule_items_and_source_types(
    db_uri: str,
) -> None:
    store = SqlAlchemyWatcherStore(db_uri)
    schedules = SqlAlchemyRoleScheduleStore(db_uri)
    items = SqlAlchemyWatchItemStore(db_uri)
    assignments = SqlAlchemySubscriptionStore(db_uri)
    watcher = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    other = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="Ticket watcher")
    store.update(watcher.id, owner_user_id=ALICE, conversation_id=SESSION_ID)
    for role_key in (watcher.role_key, other.role_key, "custodian"):
        schedules.save(role_key, owner_user_id=ALICE, rrule=HOURLY, timezone="UTC", next_run_at=1)
    seen = [WatchObservation(source="github", source_key="https://example.com/1", offsets={})]
    for each in (watcher, other):
        items.observe(each, seen, session_id=None, now=1)
    for source_type, assigned in (
        ("github.pull_request", watcher),
        ("github.issue", watcher),
        ("jira.issue", other),
    ):
        assignments.assign_type(
            owner_user_id=ALICE, source_type=source_type, watcher_id=assigned.id
        )

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
    assert assignments.types_for_watcher(watcher.id, owner_user_id=ALICE) == []
    assert [
        (assignment.source_type, assignment.watcher_id)
        for assignment in assignments.source_watchers(owner_user_id=ALICE)
    ] == [("jira.issue", other.id)]


_UNREADABLE = WatcherProblemSource(source="slack.conversation", reason="no access")
_RUN_FAILED = "auth command failed"


def _watcher_with_session(store: SqlAlchemyWatcherStore) -> Watcher:
    created = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="Slack watcher")
    watcher = store.update(created.id, owner_user_id=ALICE, conversation_id=SESSION_ID)
    assert watcher is not None
    return watcher


def test_a_problem_report_round_trips_and_dismiss_clears_only_the_named_watchers(
    db_uri: str,
) -> None:
    store = SqlAlchemyWatcherStore(db_uri)
    slack = _watcher_with_session(store)
    jira = store.create(uuid.uuid4().hex, owner_user_id=ALICE, name="Jira watcher")
    report = WatcherProblem(
        reported_at=100,
        sources=(
            WatcherProblemSource(
                source="slack.conversation",
                reason="the privacy filter hides the text",
                source_key="https://chat.example.com/archives/D0123",
            ),
        ),
        note="Ask the admin for access.",
    )
    store.set_problem(slack.id, report, owner_user_id=ALICE)
    store.set_problem(
        jira.id, WatcherProblem(reported_at=100, note="No access."), owner_user_id=ALICE
    )

    saved = store.get(slack.id, owner_user_id=ALICE)
    assert saved is not None and saved.problem == report
    assert store.set_problem(slack.id, None, owner_user_id=BOB) is None
    assert store.clear_problems([slack.id], owner_user_id=BOB) == 0
    assert store.clear_problems([slack.id, slack.id], owner_user_id=ALICE) == 1
    cleared = store.get(slack.id, owner_user_id=ALICE)
    untouched = store.get(jira.id, owner_user_id=ALICE)
    assert cleared is not None and cleared.problem is None
    assert untouched is not None and untouched.problem is not None


@pytest.mark.parametrize(
    ("reported_at", "error", "expected"),
    [
        (None, None, None),
        (50, None, None),
        (150, None, WatcherProblem(reported_at=150, sources=(_UNREADABLE,))),
        (50, _RUN_FAILED, WatcherProblem(reported_at=200, run_error=_RUN_FAILED)),
        (
            150,
            _RUN_FAILED,
            WatcherProblem(reported_at=200, sources=(_UNREADABLE,), run_error=_RUN_FAILED),
        ),
    ],
    ids=["no-report", "earlier-run", "this-run", "failed-run", "failed-after-report"],
)
def test_a_run_end_settles_the_problem_report(
    db_uri: str, reported_at: int | None, error: str | None, expected: WatcherProblem | None
) -> None:
    store = SqlAlchemyWatcherStore(db_uri)
    watcher = _watcher_with_session(store)
    if reported_at is not None:
        store.set_problem(
            watcher.id,
            WatcherProblem(reported_at=reported_at, sources=(_UNREADABLE,)),
            owner_user_id=ALICE,
        )

    settled = store.settle_run_problem(SESSION_ID, started_at=100, error=error, now=200)

    assert settled is not None and settled.problem == expected
    assert store.settle_run_problem(uuid.uuid4().hex, started_at=100, error=None, now=200) is None
