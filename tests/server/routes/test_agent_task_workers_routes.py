"""Tests for the GlobalHub worker routes: session adoption and task rosters."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}


def _task(db_uri: str, title: str, owner: str = ALICE_ID) -> str:
    task_id = uuid.uuid4().hex
    SqlAlchemyTaskStore(db_uri).create(
        task_id, owner_user_id=owner, title=title, goal=f"Finish {title}"
    )
    return task_id


def _session(db_uri: str, owner: str = ALICE_ID) -> str:
    session = SqlAlchemyConversationStore(db_uri).create_conversation(title="Fix S3 retries")
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(owner)
    permissions.grant(owner, session.id, LEVEL_OWNER)
    return session.id


async def _adopt(
    client: httpx.AsyncClient,
    session_id: str,
    task_id: str,
    headers: dict[str, str] = ALICE,
    **fields: Any,
) -> httpx.Response:
    return await client.post(
        f"/v1/agent-tasks/sessions/{session_id}/adopt",
        json={"task_id": task_id, **fields},
        headers=headers,
    )


async def test_a_session_has_one_worker_per_task_and_adopting_again_retitles_it(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id, other_task_id = _task(db_uri, "S3 retries"), _task(db_uri, "Dashboards")
    session_id = _session(db_uri)

    first = (await _adopt(auth_client, session_id, task_id, title="Reading logs")).json()
    retitled = (await _adopt(auth_client, session_id, task_id, title="Fixing retries")).json()
    untitled = (await _adopt(auth_client, session_id, task_id)).json()
    elsewhere = (await _adopt(auth_client, session_id, other_task_id)).json()
    roster = await auth_client.get(f"/v1/agent-tasks/{task_id}/workers", headers=ALICE)

    assert [adoption["already_bound"] for adoption in (first, retitled, untitled)] == [
        False,
        True,
        True,
    ]
    assert first["worker_id"] == retitled["worker_id"] == untitled["worker_id"]
    assert (elsewhere["already_bound"], elsewhere["worker_id"] == first["worker_id"]) == (
        False,
        False,
    )
    assert [
        (worker["id"], worker["task_id"], worker["session_id"], worker["title"])
        for worker in roster.json()["data"]
    ] == [(first["worker_id"], task_id, session_id, "Fixing retries")]


async def test_adoption_and_rosters_need_the_callers_own_task_and_session(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id, bobs_task_id = _task(db_uri, "S3 retries"), _task(db_uri, "Bob's", owner=BOB_ID)
    session_id, bobs_session_id = _session(db_uri), _session(db_uri, owner=BOB_ID)

    statuses = [
        (await _adopt(auth_client, session_id, bobs_task_id)).status_code,
        (await _adopt(auth_client, bobs_session_id, task_id)).status_code,
        (await _adopt(auth_client, uuid.uuid4().hex, task_id)).status_code,
        (await _adopt(auth_client, session_id, task_id, title="  ")).status_code,
        (await auth_client.get(f"/v1/agent-tasks/{task_id}/workers", headers=BOB)).status_code,
    ]

    assert statuses == [404, 404, 404, 422, 404]
    roster = await auth_client.get(f"/v1/agent-tasks/{task_id}/workers", headers=ALICE)
    assert roster.json()["data"] == []
