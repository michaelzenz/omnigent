"""Tests for the routes of users' unsent comments on agent replies."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import httpx
import pytest

from omnigent.entities.conversation import MessageData, NewConversationItem
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}
CAROL = {"X-Forwarded-Email": "carol@example.com"}
REPLY = "The retry loop waits too long."


class _Session:
    def __init__(self, session_id: str, reply_id: str, question_id: str, draft_id: str) -> None:
        self.id = session_id
        self.reply_id = reply_id
        self.question_id = question_id
        self.draft_id = draft_id

    @property
    def route(self) -> str:
        return f"/v1/sessions/{self.id}/agent-text-comments"


@pytest.fixture()
def session(db_uri: str) -> _Session:
    """Alice's session, which Bob can edit: a question, a reply and a reply in progress."""
    conversations = SqlAlchemyConversationStore(db_uri)
    session_id = conversations.create_conversation(title="Retries").id
    reply_id, question_id, draft_id = (uuid.uuid4().hex for _ in range(3))

    def item(
        stable_id: str,
        role: Literal["user", "assistant"],
        text: str,
        status: Literal["completed", "in_progress"] = "completed",
    ) -> NewConversationItem:
        kind = "output_text" if role == "assistant" else "input_text"
        agent = "test" if role == "assistant" else None
        return NewConversationItem(
            type="message",
            response_id="resp_1",
            data=MessageData(role=role, agent=agent, content=[{"type": kind, "text": text}]),
            stable_id=stable_id,
            status=status,
        )

    conversations.append(
        session_id,
        [
            item(question_id, "user", "Why is it slow?"),
            item(reply_id, "assistant", REPLY),
            item(draft_id, "assistant", "Still thinking", status="in_progress"),
        ],
    )
    permissions = SqlAlchemyPermissionStore(db_uri)
    for user_id in (ALICE_ID, BOB_ID):
        permissions.ensure_user(user_id)
    permissions.grant(ALICE_ID, session_id, LEVEL_OWNER)
    permissions.grant(BOB_ID, session_id, LEVEL_EDIT)
    return _Session(session_id, reply_id, question_id, draft_id)


def _comment(session: _Session, **fields: Any) -> dict[str, Any]:
    return {
        "conversation_item_id": session.reply_id,
        "start_offset": 4,
        "end_offset": 14,
        "selected_text": "retry loop",
        "prefix_context": "The ",
        "suffix_context": " waits",
        "body": "Use a backoff",
        **fields,
    }


async def test_each_user_has_their_own_batch(
    auth_client: httpx.AsyncClient, session: _Session
) -> None:
    alices = (await auth_client.post(session.route, json=_comment(session), headers=ALICE)).json()
    bobs = (
        await auth_client.post(session.route, json=_comment(session, body="Mine"), headers=BOB)
    ).json()

    listed = (await auth_client.get(session.route, headers=ALICE)).json()
    bob_edits = await auth_client.patch(
        f"{session.route}/{alices['id']}", json={"body": "Changed"}, headers=BOB
    )
    bob_deletes = await auth_client.delete(f"{session.route}/{alices['id']}", headers=BOB)
    edited = await auth_client.patch(
        f"{session.route}/{alices['id']}", json={"body": "Use jitter"}, headers=ALICE
    )
    sent = await auth_client.post(
        f"{session.route}/delete-batch",
        json={"comment_ids": [alices["id"], bobs["id"]]},
        headers=ALICE,
    )

    assert [(comment["id"], comment["body"]) for comment in listed] == [
        (alices["id"], "Use a backoff")
    ]
    assert (bob_edits.status_code, bob_deletes.status_code) == (404, 404)
    assert edited.json()["body"] == "Use jitter"
    assert sent.json() == {"deleted_comment_ids": [alices["id"]]}
    assert (await auth_client.get(session.route, headers=ALICE)).json() == []
    bobs_batch = (await auth_client.get(session.route, headers=BOB)).json()
    assert [comment["id"] for comment in bobs_batch] == [bobs["id"]]


async def test_only_completed_agent_text_can_take_a_comment(
    auth_client: httpx.AsyncClient, session: _Session
) -> None:
    async def status(**fields: Any) -> int:
        response = await auth_client.post(
            session.route, json=_comment(session, **fields), headers=ALICE
        )
        return response.status_code

    statuses = [
        await status(conversation_item_id=session.question_id),
        await status(conversation_item_id=session.draft_id),
        await status(conversation_item_id=uuid.uuid4().hex),
        await status(end_offset=20),
        await status(conversation_item_id="not-an-id"),
        await status(body="  "),
    ]

    assert statuses == [400, 400, 404, 400, 422, 422]
    assert (await auth_client.get(session.route, headers=ALICE)).json() == []


async def test_a_user_without_access_cannot_comment(
    auth_client: httpx.AsyncClient, session: _Session
) -> None:
    listed = await auth_client.get(session.route, headers=CAROL)
    added = await auth_client.post(session.route, json=_comment(session), headers=CAROL)

    assert listed.status_code in (403, 404)
    assert added.status_code in (403, 404)
