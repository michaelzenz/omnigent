"""Tests for the store of users' unsent comments on agent text."""

from __future__ import annotations

import uuid

import pytest

from omnigent.entities import AgentTextComment
from omnigent.stores.agent_text_comment_store.sqlalchemy_store import (
    SqlAlchemyAgentTextCommentStore,
)
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyAgentTextCommentStore:
    return SqlAlchemyAgentTextCommentStore(db_uri)


def _add(
    store: SqlAlchemyAgentTextCommentStore, owner: str, session_id: str, body: str = "Fix this"
) -> AgentTextComment:
    return store.add(
        uuid.uuid4().hex,
        owner_user_id=owner,
        conversation_id=session_id,
        conversation_item_id=uuid.uuid4().hex,
        start_offset=0,
        end_offset=4,
        selected_text="Test",
        prefix_context="",
        suffix_context=" text",
        body=body,
    )


def test_each_user_keeps_their_own_batch_in_each_session(
    store: SqlAlchemyAgentTextCommentStore,
) -> None:
    session_id, other_session = uuid.uuid4().hex, uuid.uuid4().hex
    first, second = _add(store, ALICE, session_id), _add(store, ALICE, session_id)
    bobs = _add(store, BOB, session_id)
    _add(store, ALICE, other_session)

    by_alice = store.update(first.id, owner_user_id=ALICE, conversation_id=session_id, body="New")
    by_bob = store.update(first.id, owner_user_id=BOB, conversation_id=session_id, body="Bob's")
    deleted = store.delete(
        [first.id, bobs.id, "not-an-id"], owner_user_id=ALICE, conversation_id=session_id
    )

    assert by_alice is not None and by_alice.body == "New"
    assert by_bob is None
    assert deleted == [first.id]
    assert [comment.id for comment in store.list(session_id, owner_user_id=ALICE)] == [second.id]
    assert [comment.id for comment in store.list(session_id, owner_user_id=BOB)] == [bobs.id]
    assert (
        store.update("not-an-id", owner_user_id=ALICE, conversation_id=session_id, body="x")
        is None
    )


async def test_deleting_a_session_deletes_its_comments(
    store: SqlAlchemyAgentTextCommentStore, db_uri: str
) -> None:
    conversations = SqlAlchemyConversationStore(db_uri)
    session_id = conversations.create_conversation(title="Review").id
    _add(store, ALICE, session_id)

    await conversations.delete_conversation(session_id)

    assert store.list(session_id, owner_user_id=ALICE) == []
