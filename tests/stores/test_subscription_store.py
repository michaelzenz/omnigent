"""Tests for the store of the sources that GlobalHub sessions follow."""

from __future__ import annotations

import itertools
import uuid

import pytest

from omnigent.db.db_models import workspace_scope
from omnigent.entities import SESSION_EVENT_SOURCE, Subscription
from omnigent.stores.subscription_store import (
    MAX_SOURCE_KEY_CHARS,
    MAX_SOURCE_TYPE_CHARS,
    MAX_SUBSCRIPTION_NOTE_CHARS,
    MAX_SUBSCRIPTION_NOTE_WORDS,
    SourceError,
    SourceTypeUse,
    is_reserved_source_type,
    normalize_source_key,
    normalize_source_type,
)
from omnigent.stores.subscription_store import sqlalchemy_store as subscription_tables
from omnigent.stores.subscription_store.sqlalchemy_store import SqlAlchemySubscriptionStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
PULL_REQUEST = "github.pull_request"
PR = "https://example.com/example-org/repo/pull/12"
OTHER_PR = "https://example.com/example-org/repo/pull/13"
TICKET = "https://example.com/browse/STORAGE-7"
THREAD = "example-org/C0123/1700000000.000100"


@pytest.fixture()
def store(db_uri: str, monkeypatch: pytest.MonkeyPatch) -> SqlAlchemySubscriptionStore:
    seconds = itertools.count(1_000)
    monkeypatch.setattr(subscription_tables, "now_epoch", lambda: next(seconds))
    return SqlAlchemySubscriptionStore(db_uri)


def _follow(
    store: SqlAlchemySubscriptionStore,
    session_id: str,
    source_type: str,
    source_key: str,
    *,
    owner: str = ALICE,
) -> Subscription:
    return store.subscribe(
        owner_user_id=owner, session_id=session_id, source_type=source_type, source_key=source_key
    ).subscription


def test_a_source_type_is_one_lowercase_dotted_name() -> None:
    longest = "a." + "b" * (MAX_SOURCE_TYPE_CHARS - 2)

    assert normalize_source_type(" GitHub.Pull_Request ") == PULL_REQUEST
    assert normalize_source_type("example-system.issue.comment") == "example-system.issue.comment"
    assert normalize_source_type(longest) == longest
    for unusable in (
        "github",
        "github.",
        ".issue",
        "git hub.issue",
        "git/hub.issue",
        f"{longest}b",
    ):
        with pytest.raises(SourceError):
            normalize_source_type(unusable)
    assert is_reserved_source_type(SESSION_EVENT_SOURCE)
    assert not is_reserved_source_type(PULL_REQUEST)


def test_a_source_key_is_trimmed_and_a_web_address_loses_its_fragment_and_final_slash() -> None:
    assert normalize_source_key(f"  {PR}/#discussion_r1 ") == PR
    assert (
        normalize_source_key("https://example.com/search/?q=storage#top")
        == "https://example.com/search?q=storage"
    )
    assert normalize_source_key(" example-org/channel/ ") == "example-org/channel/"
    for unusable in ("", "   ", "k" * (MAX_SOURCE_KEY_CHARS + 1)):
        with pytest.raises(SourceError):
            normalize_source_key(unusable)


def test_a_session_follows_a_key_once_and_a_new_subscribe_replaces_its_type(
    store: SqlAlchemySubscriptionStore,
) -> None:
    session_id = uuid.uuid4().hex
    dashed = str(uuid.UUID(hex=session_id))

    first = store.subscribe(
        owner_user_id=ALICE,
        session_id=dashed,
        source_type=" GitHub.Pull_Request ",
        source_key=f" {PR}/#discussion_r1",
        note="  Review the fix  ",
    ).subscription
    note_kept = _follow(store, session_id, "github.issue", PR)
    note_cleared = store.subscribe(
        owner_user_id=ALICE,
        session_id=session_id,
        source_type="github.issue",
        source_key=PR,
        note=" ",
    ).subscription

    assert (first.session_id, first.source_type, first.source_key, first.note) == (
        session_id,
        PULL_REQUEST,
        PR,
        "Review the fix",
    )
    assert (
        note_kept.id,
        note_kept.source_type,
        note_kept.note,
        note_kept.created_at,
    ) == (first.id, "github.issue", "Review the fix", first.created_at)
    assert (note_cleared.id, note_cleared.note) == (first.id, None)
    assert store.list_for_session(dashed, owner_user_id=ALICE) == [note_cleared]
    with pytest.raises(SourceError):
        _follow(store, session_id, "github", PR)


def test_a_note_has_at_most_300_words(store: SqlAlchemySubscriptionStore) -> None:
    session_id = uuid.uuid4().hex

    def subscribe(note: str) -> str | None:
        return store.subscribe(
            owner_user_id=ALICE,
            session_id=session_id,
            source_type=PULL_REQUEST,
            source_key=PR,
            note=note,
        ).subscription.note

    longest = " ".join(["word"] * MAX_SUBSCRIPTION_NOTE_WORDS)

    assert subscribe(longest) == longest
    for too_long in (f"{longest} more", "n" * (MAX_SUBSCRIPTION_NOTE_CHARS + 1)):
        with pytest.raises(SourceError):
            subscribe(too_long)
    assert store.list_for_session(session_id, owner_user_id=ALICE)[0].note == longest


def test_a_type_is_new_until_one_of_the_owners_subscriptions_or_assignments_has_it(
    store: SqlAlchemySubscriptionStore,
) -> None:
    first_session, second_session, bobs_session = (uuid.uuid4().hex for _ in range(3))
    store.assign_type(owner_user_id=ALICE, source_type="jira.issue", watcher_id=uuid.uuid4().hex)

    def new_type(session_id: str, source_type: str, source_key: str, owner: str = ALICE) -> bool:
        return store.subscribe(
            owner_user_id=owner,
            session_id=session_id,
            source_type=source_type,
            source_key=source_key,
        ).new_type

    first_of_its_type = new_type(first_session, PULL_REQUEST, PR)
    second_of_its_type = new_type(second_session, PULL_REQUEST, OTHER_PR)
    assigned_type = new_type(first_session, "jira.issue", TICKET)
    reserved_type = new_type(first_session, SESSION_EVENT_SOURCE, second_session)
    first_of_bobs = new_type(bobs_session, PULL_REQUEST, PR, owner=BOB)

    assert (first_of_its_type, second_of_its_type) == (True, False)
    assert (assigned_type, reserved_type) == (False, False)
    assert first_of_bobs is True


def test_the_subscribers_of_a_key_are_the_owners_sessions_in_subscription_order(
    store: SqlAlchemySubscriptionStore,
) -> None:
    first, second, third = f"{3:032x}", f"{2:032x}", f"{1:032x}"
    for session_id in (first, second, third):
        _follow(store, session_id, PULL_REQUEST, PR)
    _follow(store, first, "github.issue", PR)
    _follow(store, second, PULL_REQUEST, OTHER_PR)
    bobs_session, other_workspace_session = uuid.uuid4().hex, uuid.uuid4().hex
    _follow(store, bobs_session, PULL_REQUEST, PR, owner=BOB)
    with workspace_scope(7):
        _follow(store, other_workspace_session, PULL_REQUEST, PR)

    assert store.subscribers(owner_user_id=ALICE, source_key=f"{PR}/") == [first, second, third]
    assert store.subscribers(owner_user_id=ALICE, source_key=OTHER_PR) == [second]
    assert store.subscribers(owner_user_id=ALICE, source_key=f"{PR}/files") == []
    assert store.subscribers(owner_user_id=BOB, source_key=PR) == [bobs_session]
    assert store.subscribers(owner_user_id=None, source_key=PR) == []
    with workspace_scope(7):
        assert store.subscribers(owner_user_id=ALICE, source_key=PR) == [other_workspace_session]


def test_unassigned_subscriptions_and_source_types_leave_out_the_servers_own_types(
    store: SqlAlchemySubscriptionStore,
) -> None:
    first_session, second_session = uuid.uuid4().hex, uuid.uuid4().hex
    jira_watcher, unused_type_watcher = uuid.uuid4().hex, uuid.uuid4().hex
    pull_request = _follow(store, first_session, PULL_REQUEST, PR)
    ticket = _follow(store, second_session, "jira.issue", TICKET)
    _follow(store, first_session, SESSION_EVENT_SOURCE, second_session)
    thread = _follow(store, second_session, "slack.thread", THREAD)
    other_pull_request = _follow(store, second_session, PULL_REQUEST, OTHER_PR)
    bobs_thread = _follow(store, uuid.uuid4().hex, "slack.thread", THREAD, owner=BOB)
    store.assign_type(owner_user_id=ALICE, source_type="jira.issue", watcher_id=jira_watcher)
    store.assign_type(
        owner_user_id=ALICE, source_type="example.ticket", watcher_id=unused_type_watcher
    )

    assert store.list_unassigned(owner_user_id=ALICE) == [
        pull_request,
        thread,
        other_pull_request,
    ]
    assert store.source_types(owner_user_id=ALICE) == [
        SourceTypeUse(
            source_type="example.ticket", watcher_id=unused_type_watcher, subscriptions=0
        ),
        SourceTypeUse(source_type=PULL_REQUEST, watcher_id=None, subscriptions=2),
        SourceTypeUse(source_type="jira.issue", watcher_id=jira_watcher, subscriptions=1),
        SourceTypeUse(source_type="slack.thread", watcher_id=None, subscriptions=1),
    ]
    assert store.list_for_types(["jira.issue", "slack.thread"], owner_user_id=ALICE) == [
        ticket,
        thread,
    ]
    assert store.list_for_types([], owner_user_id=ALICE) == []
    assert store.list_unassigned(owner_user_id=BOB) == [bobs_thread]
    assert store.source_types(owner_user_id=BOB) == [
        SourceTypeUse(source_type="slack.thread", watcher_id=None, subscriptions=1)
    ]


def test_assigning_a_type_replaces_its_watcher_until_the_type_goes_back_to_the_fallback(
    store: SqlAlchemySubscriptionStore,
) -> None:
    github_watcher, other_watcher = uuid.uuid4().hex, uuid.uuid4().hex

    first = store.assign_type(
        owner_user_id=ALICE, source_type=" GitHub.Pull_Request ", watcher_id=github_watcher
    )
    again = store.assign_type(
        owner_user_id=ALICE, source_type=PULL_REQUEST, watcher_id=github_watcher
    )
    moved = store.assign_type(
        owner_user_id=ALICE, source_type=PULL_REQUEST, watcher_id=other_watcher
    )
    issue = store.assign_type(
        owner_user_id=ALICE, source_type="github.issue", watcher_id=github_watcher
    )
    store.assign_type(owner_user_id=BOB, source_type="github.issue", watcher_id=other_watcher)
    listed = store.source_watchers(owner_user_id=ALICE)
    types_before = [
        store.types_for_watcher(watcher_id, owner_user_id=ALICE)
        for watcher_id in (github_watcher, other_watcher)
    ]
    unassigned = [
        store.unassign_type(owner_user_id=ALICE, source_type="GitHub.Pull_Request"),
        store.unassign_type(owner_user_id=ALICE, source_type=PULL_REQUEST),
    ]

    assert (first.owner_user_id, first.source_type, first.watcher_id) == (
        ALICE,
        PULL_REQUEST,
        github_watcher,
    )
    assert again == first
    assert (moved.watcher_id, moved.assigned_at > first.assigned_at) == (other_watcher, True)
    assert listed == [issue, moved]
    assert types_before == [["github.issue"], [PULL_REQUEST]]
    assert unassigned == [True, False]
    assert store.types_for_watcher(other_watcher, owner_user_id=ALICE) == []
    assert store.types_for_watcher(other_watcher, owner_user_id=BOB) == ["github.issue"]
    for refused in (SESSION_EVENT_SOURCE, "github"):
        with pytest.raises(SourceError):
            store.assign_type(owner_user_id=ALICE, source_type=refused, watcher_id=github_watcher)
    assert store.source_watchers(owner_user_id=ALICE) == [issue]


def test_a_copy_follows_the_sources_of_another_session_with_their_notes_and_start_times(
    store: SqlAlchemySubscriptionStore,
) -> None:
    source, clone, worker = (uuid.uuid4().hex for _ in range(3))
    reviewed = store.subscribe(
        owner_user_id=ALICE,
        session_id=source,
        source_type=PULL_REQUEST,
        source_key=PR,
        note="Read the review threads, not only the checks.",
    ).subscription
    _follow(store, source, "jira.issue", TICKET)
    _follow(store, source, SESSION_EVENT_SOURCE, worker)
    _follow(store, source, "slack.thread", THREAD, owner=BOB)
    _follow(store, clone, PULL_REQUEST, OTHER_PR)
    _follow(store, clone, "jira.issue", TICKET)

    copied = store.copy_session(source, clone, owner_user_id=ALICE)

    follows = store.list_for_session(clone, owner_user_id=ALICE)
    assert copied == 1
    assert [subscription.source_key for subscription in follows] == [PR, OTHER_PR, TICKET]
    assert (follows[0].note, follows[0].created_at) == (reviewed.note, reviewed.created_at)
    assert store.list_for_session(clone, owner_user_id=BOB) == []
    assert store.copy_session(source, clone, owner_user_id=ALICE) == 0


def test_unsubscribe_and_the_deletes_by_session_or_key_are_owner_scoped(
    store: SqlAlchemySubscriptionStore,
) -> None:
    leaving, staying, bobs_session = (uuid.uuid4().hex for _ in range(3))
    for session_id, source_type, source_key in (
        (leaving, PULL_REQUEST, PR),
        (leaving, PULL_REQUEST, OTHER_PR),
        (staying, PULL_REQUEST, PR),
        (staying, "jira.issue", TICKET),
        (staying, "slack.thread", THREAD),
    ):
        _follow(store, session_id, source_type, source_key)
    _follow(store, bobs_session, PULL_REQUEST, PR, owner=BOB)

    refused = [
        store.unsubscribe(owner_user_id=BOB, session_id=staying, source_key=TICKET),
        store.delete_for_session(leaving, owner_user_id=BOB),
        store.delete_for_keys([PR], owner_user_id=None),
    ]
    unsubscribed = store.unsubscribe(
        owner_user_id=ALICE, session_id=staying, source_key=f"{TICKET}/"
    )
    deleted_for_session = store.delete_for_session(leaving, owner_user_id=ALICE)
    deleted_for_key = store.delete_for_keys([PR], owner_user_id=ALICE)

    assert refused == [False, 0, 0]
    assert (unsubscribed, deleted_for_session, deleted_for_key) == (True, 2, 1)
    assert store.unsubscribe(owner_user_id=ALICE, session_id=staying, source_key=TICKET) is False
    assert store.list_for_session(leaving, owner_user_id=ALICE) == []
    assert [
        subscription.source_key
        for subscription in store.list_for_session(staying, owner_user_id=ALICE)
    ] == [THREAD]
    assert store.subscribers(owner_user_id=BOB, source_key=PR) == [bobs_session]
    assert store.delete_for_keys([], owner_user_id=ALICE) == 0
