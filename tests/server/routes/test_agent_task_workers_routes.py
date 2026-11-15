"""Tests for the GlobalHub worker routes: adoption, rosters, untrack and started sessions."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.runner.identity import (
    ORCHESTRATION_SOURCE_HEADER,
    RUNNER_TUNNEL_TOKEN_HEADER,
    token_bound_runner_id,
)
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server import agent_task_worker_sessions
from omnigent.server.agent_task_delivery import SendResult
from omnigent.server.app import create_app
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, UnifiedAuthProvider
from omnigent.server.routes._host_worktree import (
    WORKTREE_ROOT_LABEL_KEY,
    CreatedWorktree,
    worktree_root_fingerprint,
)
from omnigent.server.schemas import SessionGitOptions
from omnigent.server.server_config import PickerCuration
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store import CREATED_BY_SESSION_LABEL_KEY
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": BOB_ID}
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
REPO = "/Users/alice/repo"
PR = "https://github.com/example-org/repo/pull/12"


@pytest.fixture()
def hub_app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    """A multi-user app with projects and hosts, where sessions start for tasks."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        auth_provider=UnifiedAuthProvider(source="header", local_single_user=False),
        permission_store=SqlAlchemyPermissionStore(db_uri),
        project_store=SqlAlchemyProjectStore(db_uri),
        host_store=HostStore(db_uri),
    )


@pytest_asyncio.fixture()
async def hub_client(hub_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=hub_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _task(db_uri: str, title: str, owner: str = ALICE_ID, **fields: Any) -> str:
    task_id = uuid.uuid4().hex
    SqlAlchemyTaskStore(db_uri).create(
        task_id, owner_user_id=owner, title=title, goal=f"Finish {title}", **fields
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


def _agent(db_uri: str, name: str) -> None:
    SqlAlchemyAgentStore(db_uri).create(uuid.uuid4().hex, name, f"bundles/{name}.tar.gz")


def _calling_session(db_uri: str) -> tuple[str, dict[str, str]]:
    """Return one of Alice's sessions and the headers that its runner sends."""
    session_id = _session(db_uri)
    SqlAlchemyConversationStore(db_uri).set_runner_id(
        session_id, token_bound_runner_id("manager-runner")
    )
    return session_id, {
        **ALICE,
        ORCHESTRATION_SOURCE_HEADER: session_id,
        RUNNER_TUNNEL_TOKEN_HEADER: "manager-runner",
    }


def _accept_workspaces(monkeypatch: pytest.MonkeyPatch) -> None:
    async def canonical(*, workspace: str, **_: Any) -> str:
        return workspace

    monkeypatch.setattr(agent_task_worker_sessions, "validate_existing_host_workspace", canonical)


class _Runner:
    """Takes the first messages in place of the sessions' runners."""

    def __init__(self, result: SendResult) -> None:
        self.result = result
        self.messages: list[tuple[str, str, str | None]] = []

    async def send(
        self, session_id: str, text: str, *, created_by: str | None, deps: Any
    ) -> SendResult:
        self.messages.append((session_id, text, created_by))
        return self.result


def _runner(app: FastAPI, monkeypatch: pytest.MonkeyPatch, result: SendResult) -> _Runner:
    runner = _Runner(result)
    monkeypatch.setattr(app.state.worker_session_starter, "_send", runner.send)
    return runner


async def _first_messages_sent(app: FastAPI) -> None:
    await asyncio.gather(*list(app.state.worker_session_starter._sends))


async def _start(
    client: httpx.AsyncClient,
    task_id: str,
    headers: dict[str, str] = ALICE,
    **fields: Any,
) -> httpx.Response:
    body = {
        "host_id": HOST_ID,
        "workspace": REPO,
        "title": "Review PR 12",
        "message": f"Review {PR}",
        **fields,
    }
    return await client.post(
        f"/v1/agent-tasks/{task_id}/workers/start", json=body, headers=headers
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


async def test_untrack_removes_the_worker_and_leaves_the_session(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    task_id, session_id = _task(db_uri, "S3 retries"), _session(db_uri)
    worker_id = (await _adopt(auth_client, session_id, task_id)).json()["worker_id"]
    untrack = f"/v1/task-workers/{worker_id}/untrack"

    by_bob = await auth_client.post(untrack, headers=BOB)
    untracked = await auth_client.post(untrack, headers=ALICE)
    again = await auth_client.post(untrack, headers=ALICE)
    roster = await auth_client.get(f"/v1/agent-tasks/{task_id}/workers", headers=ALICE)
    readopted = (await _adopt(auth_client, session_id, task_id)).json()

    assert (by_bob.status_code, again.status_code) == (404, 404)
    assert untracked.json() == {"object": "agent.task.worker", "id": worker_id, "deleted": True}
    assert roster.json()["data"] == []
    assert SqlAlchemyConversationStore(db_uri).get_conversation(session_id) is not None
    assert (readopted["already_bound"], readopted["worker_id"] == worker_id) == (False, False)


async def test_a_started_session_is_the_tasks_worker_before_its_first_message(
    hub_app: FastAPI, hub_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _accept_workspaces(monkeypatch)
    runner = _runner(hub_app, monkeypatch, SendResult("sent"))
    _agent(db_uri, "claude-native-ui")
    task_id = _task(db_uri, "PR 12")
    manager_session_id, manager_runner = _calling_session(db_uri)

    started = await _start(
        hub_client,
        task_id,
        manager_runner,
        agent="claude-native-ui",
        project_name="Reviews",
        model="databricks-claude-sonnet-4-6",
        reasoning_effort="high",
    )
    await _first_messages_sent(hub_app)

    assert started.status_code == 201
    body = started.json()
    session = SqlAlchemyConversationStore(db_uri).get_conversation(body["session_id"])
    assert session is not None
    [project] = SqlAlchemyProjectStore(db_uri).list(user_id=ALICE_ID)
    assert (project.name, body["project_id"], session.project_id) == (
        "Reviews",
        project.id,
        project.id,
    )
    assert (body["task_id"], body["agent"], body["workspace"], body["git_branch"]) == (
        task_id,
        "claude-native-ui",
        REPO,
        None,
    )
    assert (session.title, session.host_id, session.workspace) == ("Review PR 12", HOST_ID, REPO)
    assert (session.model_override, session.reasoning_effort) == (
        "databricks-claude-sonnet-4-6",
        "high",
    )
    assert (
        session.labels.items()
        >= {
            "omnigent.ui": "terminal",
            "omnigent.wrapper": "claude-code-native-ui",
            CREATED_BY_SESSION_LABEL_KEY: manager_session_id,
        }.items()
    )
    grant = SqlAlchemyPermissionStore(db_uri).get(ALICE_ID, session.id)
    assert grant is not None and grant.level == LEVEL_OWNER
    roster = await hub_client.get(f"/v1/agent-tasks/{task_id}/workers", headers=ALICE)
    assert [
        (worker["id"], worker["session_id"], worker["title"]) for worker in roster.json()["data"]
    ] == [(body["worker_id"], session.id, "Review PR 12")]
    assert runner.messages == [(session.id, f"Review {PR}", ALICE_ID)]


async def test_a_session_started_in_a_worktree_reports_a_first_message_that_failed(
    hub_app: FastAPI, hub_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _accept_workspaces(monkeypatch)
    _runner(hub_app, monkeypatch, SendResult("unreachable", "no runner can be reached or woken"))
    _agent(db_uri, "codex-native-ui")
    monkeypatch.setattr(
        hub_app.state, "picker_curation", PickerCuration(default_agent_name="codex-native-ui")
    )
    worktree_path = "/Users/alice/repo-worktrees/review-12"
    worktrees: list[tuple[str, str, SessionGitOptions]] = []

    async def create_worktree(
        *, host_id: str, source_repo: str, git: SessionGitOptions, request: Any
    ) -> CreatedWorktree:
        worktrees.append((host_id, source_repo, git))
        return CreatedWorktree(
            worktree_path=worktree_path, branch=git.branch_name, workspace=worktree_path
        )

    monkeypatch.setattr(agent_task_worker_sessions, "_create_session_worktree", create_worktree)
    manager_id = (
        SqlAlchemyManagerStore(db_uri)
        .create(uuid.uuid4().hex, owner_user_id=ALICE_ID, title="Reviews")
        .id
    )
    task_id = _task(db_uri, "PR 12", manager_id=manager_id)

    started = await _start(
        hub_client, task_id, git={"branch_name": "review-12", "existing_branch": True}
    )
    await _first_messages_sent(hub_app)

    body = started.json()
    session = SqlAlchemyConversationStore(db_uri).get_conversation(body["session_id"])
    assert session is not None
    assert (body["agent"], body["workspace"], body["git_branch"]) == (
        "codex-native-ui",
        worktree_path,
        "review-12",
    )
    assert [(host, repo, git.existing_branch) for host, repo, git in worktrees] == [
        (HOST_ID, REPO, True)
    ]
    assert (session.workspace, session.git_branch) == (worktree_path, "review-12")
    assert session.labels.get(WORKTREE_ROOT_LABEL_KEY) == worktree_root_fingerprint(worktree_path)
    assert CREATED_BY_SESSION_LABEL_KEY not in session.labels
    [failed] = SqlAlchemyTaskEventStore(db_uri).list(
        owner_user_id=ALICE_ID, event_type="session.start.failed"
    )
    assert (failed.manager_id, failed.source_session_id) == (manager_id, session.id)
    assert failed.payload is not None
    assert failed.payload["error"] == "no runner can be reached or woken"


async def test_starting_a_session_needs_the_callers_task_agent_and_host_and_a_private_caller(
    hub_app: FastAPI, hub_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub_app.state, "picker_curation", PickerCuration())
    _agent(db_uri, "claude-native-ui")
    task_id, bobs_task_id = _task(db_uri, "PR 12"), _task(db_uri, "Bob's", owner=BOB_ID)
    bobs_host_id = "1f2e3d4c5b6a79808796a5b4c3d2e1f0"
    hub_app.state.host_store.upsert_on_connect(bobs_host_id, "bob-laptop", BOB_ID)
    shared_session_id, shared_runner = _calling_session(db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(BOB_ID)
    permissions.grant(BOB_ID, shared_session_id, LEVEL_EDIT)
    claude = {"agent": "claude-native-ui"}

    statuses = [
        (await _start(hub_client, bobs_task_id, **claude)).status_code,
        (await _start(hub_client, uuid.uuid4().hex, **claude)).status_code,
        (await _start(hub_client, task_id)).status_code,
        (await _start(hub_client, task_id, agent="missing-agent")).status_code,
        (await _start(hub_client, task_id, agent="onih-pmv2")).status_code,
        (await _start(hub_client, task_id, reasoning_effort="extreme", **claude)).status_code,
        (await _start(hub_client, task_id, workspace=" ", **claude)).status_code,
        (await _start(hub_client, task_id, host_id=bobs_host_id, **claude)).status_code,
        (await _start(hub_client, task_id, shared_runner, **claude)).status_code,
    ]

    assert statuses == [404, 404, 400, 400, 400, 400, 422, 403, 403]
    roster = await hub_client.get(f"/v1/agent-tasks/{task_id}/workers", headers=ALICE)
    assert roster.json()["data"] == []
