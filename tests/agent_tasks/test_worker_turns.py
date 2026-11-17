"""Tests for reading the recent turns of a worker's session, for the board search."""

from __future__ import annotations

import uuid
from typing import Any

from omnigent.agent_tasks.worker_turns import ConversationSessions
from omnigent.entities.conversation import MessageData, NewConversationItem
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


def _message(role: str, text: str, **fields: Any) -> NewConversationItem:
    if role == "assistant":
        fields.setdefault("agent", "worker")
    block = "input_text" if role == "user" else "output_text"
    return NewConversationItem(
        type="message",
        response_id="turn",
        data=MessageData(role=role, content=[{"type": block, "text": text}], **fields),
    )


def test_a_session_reads_as_its_last_three_turns_with_the_last_reply_of_each(
    db_uri: str,
) -> None:
    conversations = SqlAlchemyConversationStore(db_uri)
    session_id = conversations.create_conversation(title="Flag rollout").id
    empty_id = conversations.create_conversation(title="Empty").id
    conversations.append(
        session_id,
        [
            _message("user", "first ask"),
            _message("assistant", "first answer"),
            _message("user", "babysit the flag rollout"),
            _message("assistant", "checking"),
            _message("assistant", "watching it"),
            _message("user", "internal reminder", is_meta=True),
            _message("user", "any change?"),
            _message("assistant", "still green"),
            _message("user", "and now?"),
        ],
    )
    sessions = ConversationSessions(conversations)

    text = sessions.recent_turns(0, session_id)
    changed = sessions.changed_at(0, [session_id, empty_id, uuid.uuid4().hex])

    assert text == (
        "User: babysit the flag rollout\n\nAgent: watching it\n\n"
        "User: any change?\n\nAgent: still green\n\nUser: and now?"
    )
    assert sessions.recent_turns(0, empty_id) == ""
    assert set(changed) == {session_id, empty_id}
