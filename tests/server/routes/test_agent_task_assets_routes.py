"""Tests for the GlobalHub task asset routes (``/v1/agent-tasks/{task_id}/assets``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

ALICE_ID = "alice@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": "bob@example.com"}
DOC_URL = "https://example.com/design-doc"


def _task(db_uri: str) -> str:
    task_id = uuid.uuid4().hex
    SqlAlchemyTaskStore(db_uri).create(task_id, owner_user_id=ALICE_ID, title="S3 retries")
    return task_id


async def _attach(
    client: httpx.AsyncClient, task_id: str, headers: dict[str, str] = ALICE, **fields: Any
) -> httpx.Response:
    return await client.post(
        f"/v1/agent-tasks/{task_id}/assets",
        json={"url": DOC_URL, "title": "Design doc", **fields},
        headers=headers,
    )


async def test_posting_a_url_again_updates_the_tasks_one_asset_for_it(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id = _task(db_uri)
    first_session, second_session = uuid.uuid4().hex, uuid.uuid4().hex

    first = await _attach(
        auth_client, task_id, category="runbooks", add_related_session_ids=[first_session]
    )
    again = await _attach(
        auth_client, task_id, title="Design doc v2", add_related_session_ids=[second_session]
    )
    other_url = await _attach(auth_client, task_id, url="https://example.com/pull/1", title="Fix")

    assert (again.json()["id"], again.json()["title"], again.json()["category"]) == (
        first.json()["id"],
        "Design doc v2",
        "runbooks",
    )
    assert again.json()["related_session_ids"] == [first_session, second_session]
    assert (other_url.json()["category"], other_url.json()["id"] != first.json()["id"]) == (
        "other",
        True,
    )


async def test_assets_belong_to_the_callers_own_tasks(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id, other_task_id = _task(db_uri), _task(db_uri)
    asset_id = (await _attach(auth_client, task_id)).json()["id"]
    route = f"/v1/agent-tasks/{task_id}/assets/{asset_id}"

    statuses = [
        (await _attach(auth_client, task_id, headers=BOB)).status_code,
        (await _attach(auth_client, uuid.uuid4().hex)).status_code,
        (await _attach(auth_client, task_id, url="  ")).status_code,
        (await auth_client.delete(route, headers=BOB)).status_code,
        (
            await auth_client.delete(
                f"/v1/agent-tasks/{other_task_id}/assets/{asset_id}", headers=ALICE
            )
        ).status_code,
    ]
    deleted = await auth_client.delete(route, headers=ALICE)
    again = await auth_client.delete(route, headers=ALICE)

    assert statuses == [404, 404, 422, 404, 404]
    assert deleted.json() == {
        "object": "agent.task.asset",
        "id": asset_id,
        "task_id": task_id,
        "deleted": True,
    }
    assert again.status_code == 404
