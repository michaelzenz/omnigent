"""Stamped folders merge by path, persist with the session, and follow session access.

The owner's clone of a session keeps its folders; another user's clone starts with none.
"""

import asyncio
import json
from contextlib import aclosing
from pathlib import Path

import httpx
import pytest

from omnigent.runner.tool_dispatch import execute_tool
from omnigent.runtime import session_stream
from omnigent.server.auth import LEVEL_READ
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.helpers import create_test_agent

pytestmark = pytest.mark.asyncio


async def _stamp(client: httpx.AsyncClient, session_id: str, **arguments: object) -> dict:
    output = await execute_tool(
        tool_name="ui_stamp_folders",
        arguments=json.dumps(arguments),
        server_client=client,
        conversation_id=session_id,
    )
    return json.loads(output)


async def test_stamps_merge_by_path_and_reach_current_viewers(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    repo, worktree, docs = (tmp_path / name for name in ("repo", "worktree", "docs"))
    for folder in (repo, worktree, docs):
        folder.mkdir()
    agent = await create_test_agent(client)
    session_id = (await client.post("/v1/sessions", json={"agent_id": agent["id"]})).json()["id"]
    route = f"/v1/sessions/{session_id}/ui/open-folders"
    assert (await client.get(route)).json() == {"folders": []}

    async with aclosing(
        session_stream.subscribe(session_id, ready_event={"type": "ready"})
    ) as live:
        assert await anext(live) == {"type": "ready"}
        first = await _stamp(
            client,
            session_id,
            folders=[
                {"title": "Repo", "path": str(repo)},
                {"title": "Fix", "path": str(worktree)},
            ],
        )
        event = await asyncio.wait_for(anext(live), timeout=1)
    assert event["type"] == "ui.open_folders"
    assert event["folders"] == first["folders"]

    second = await _stamp(
        client,
        session_id,
        folders=[{"title": "Main repo", "path": f"{repo}/"}, {"title": "Docs", "path": str(docs)}],
        remove=[str(worktree)],
    )
    expected = [{"title": "Main repo", "path": str(repo)}, {"title": "Docs", "path": str(docs)}]
    assert second == {"folders": expected}
    assert (await client.get(route)).json() == {"folders": expected}


async def test_a_full_session_refuses_new_folders_and_keeps_its_list(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    folders = [tmp_path / f"folder-{index}" for index in range(21)]
    for folder in folders:
        folder.mkdir()
    agent = await create_test_agent(client)
    session_id = (await client.post("/v1/sessions", json={"agent_id": agent["id"]})).json()["id"]
    stamps = [{"title": folder.name, "path": str(folder)} for folder in folders]
    assert len((await _stamp(client, session_id, folders=stamps[:20]))["folders"]) == 20

    refused = await _stamp(client, session_id, folders=stamps[20:])

    assert "at most 20 folders" in refused["error"]
    listed = (await client.get(f"/v1/sessions/{session_id}/ui/open-folders")).json()
    assert [folder["title"] for folder in listed["folders"]] == [f.name for f in folders[:20]]


async def test_open_folders_follow_session_access(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    owner = {"X-Forwarded-Email": "alice@example.com"}
    agent = await create_test_agent(auth_client, user="alice@example.com")
    created = await auth_client.post("/v1/sessions", json={"agent_id": agent["id"]}, headers=owner)
    session_id = created.json()["id"]
    route = f"/v1/sessions/{session_id}/ui/open-folders"
    SqlAlchemyPermissionStore(db_uri).grant("reader@example.com", session_id, LEVEL_READ)
    stamp = {"folders": [{"title": "Repo", "path": "/srv/repo"}]}

    assert (await auth_client.post(route, json=stamp, headers=owner)).status_code == 200
    reader = {"X-Forwarded-Email": "reader@example.com"}
    assert (await auth_client.get(route, headers=reader)).json() == stamp
    assert (await auth_client.post(route, json=stamp, headers=reader)).status_code in (403, 404)
    stranger = {"X-Forwarded-Email": "stranger@example.com"}
    assert (await auth_client.get(route, headers=stranger)).status_code in (403, 404)
    missing = await auth_client.get("/v1/sessions/missing/ui/open-folders", headers=owner)
    assert missing.status_code == 404

    owner_clone = await auth_client.post(f"/v1/sessions/{session_id}/fork", json={}, headers=owner)
    reader_clone = await auth_client.post(
        f"/v1/sessions/{session_id}/fork", json={}, headers=reader
    )
    assert (owner_clone.status_code, reader_clone.status_code) == (201, 201)
    owner_clone_route = f"/v1/sessions/{owner_clone.json()['id']}/ui/open-folders"
    reader_clone_route = f"/v1/sessions/{reader_clone.json()['id']}/ui/open-folders"
    assert (await auth_client.get(owner_clone_route, headers=owner)).json() == stamp
    assert (await auth_client.get(reader_clone_route, headers=reader)).json() == {"folders": []}
