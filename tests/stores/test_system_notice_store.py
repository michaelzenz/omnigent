"""Tests for the GlobalHub system notice store."""

from __future__ import annotations

import itertools

import pytest

from omnigent.stores.system_notice_store.sqlalchemy_store import SqlAlchemySystemNoticeStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
CUSTODIAN_SESSION = f"{300:032x}"
CLEANUP_ACTIONS = [
    {
        "id": "confirm_cleanup",
        "label": "Delete selected sessions",
        "inputs": [
            {"id": "delete_a", "kind": "toggle", "label": "Session A", "group": "Host one"},
        ],
    }
]
CHANGES = [
    {"id": "delete_session__a", "kind": "delete_session", "session": "a", "summary": "Idle"}
]
# Creation order ties on the one-second created_at, then falls back to id.
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemySystemNoticeStore:
    return SqlAlchemySystemNoticeStore(db_uri)


def test_publishing_replaces_the_owners_active_notice(store: SqlAlchemySystemNoticeStore) -> None:
    store.publish(next(_ids), owner_user_id=ALICE, title="Old cleanup")
    published = store.publish(
        next(_ids),
        owner_user_id=ALICE,
        title="Stale session cleanup",
        body_kind="html",
        body="<p>Toggle the sessions to delete.</p>",
        actions=CLEANUP_ACTIONS,
        publisher_session_id=CUSTODIAN_SESSION,
        publisher_label="Task custodian",
        changes=CHANGES,
    )
    bobs = store.publish(next(_ids), owner_user_id=BOB, title="Bob's notice")

    active = store.get_active(owner_user_id=ALICE)

    assert active is not None and active.id == published.id
    assert (active.state, active.body_kind, active.actions, active.changes) == (
        "active",
        "html",
        CLEANUP_ACTIONS,
        CHANGES,
    )
    assert (active.publisher_session_id, active.publisher_label) == (
        CUSTODIAN_SESSION,
        "Task custodian",
    )
    # The publish resolved the replaced notice, so dismissing the new one
    # leaves none active.
    resolved = store.resolve_active(owner_user_id=ALICE)
    assert resolved is not None and resolved.id == published.id
    assert store.get_active(owner_user_id=ALICE) is None
    bobs_active = store.get_active(owner_user_id=BOB)
    assert bobs_active is not None and bobs_active.id == bobs.id


def test_resolving_leaves_no_active_notice_and_repeats_safely(
    store: SqlAlchemySystemNoticeStore,
) -> None:
    notice = store.publish(next(_ids), owner_user_id=None, title="Single-user notice")

    resolved = store.resolve_active(owner_user_id=None)

    assert resolved is not None
    assert (resolved.id, resolved.state, resolved.resolved_at is not None) == (
        notice.id,
        "resolved",
        True,
    )
    assert (resolved.actions, resolved.body, resolved.changes) == ([], None, [])
    assert store.resolve_active(owner_user_id=None) is None
    assert store.get_active(owner_user_id=None) is None
