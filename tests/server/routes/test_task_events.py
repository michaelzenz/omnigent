"""Tests for managed task event routes (``/v1/task-events``)."""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_MANAGER_AGENT_NAME,
    resolve_task_agent_id,
)
from omnigent.entities import EventTag, TaskTag
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from tests.server.routes.agent_task_api import patch_host_session_launch, put_agent_role_profile


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _seed_live_host(db_uri: str, seed: str) -> str:
    host_id = _uid(seed)
    HostStore(db_uri).upsert_on_connect(host_id, seed, RESERVED_USER_LOCAL)
    return host_id


def _register_manager(
    db_uri: str,
    *,
    conversation_id: str,
    agent_id: str,
    owner_user_id: str = "__anonymous__",
    host_id: str | None = None,
) -> None:
    SqlAlchemyConversationStore(db_uri).create_conversation(
        conversation_id=conversation_id,
        title="Task event manager",
        agent_id=agent_id,
        host_id=host_id,
        workspace="/tmp/task-event-manager",
    )
    SqlAlchemyManagerStore(db_uri).upsert(
        conversation_id,
        owner_user_id=owner_user_id,
        role_key="manager:default",
        description="Owns routed build events.",
    )


@pytest.fixture(autouse=True)
def _patch_host_session_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_host_session_launch(monkeypatch)


@pytest_asyncio.fixture()
async def manager_agent_profile_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    _seed_live_host(db_uri, "host_test")
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


@pytest_asyncio.fixture()
def task_event_store(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


async def _put_broker_profile(
    client: httpx.AsyncClient,
    manager_agent_profile_id: str,
) -> None:
    profile_resp = await put_agent_role_profile(
        client,
        role=TASK_BROKER_ROLE,
        agent_profile_id=manager_agent_profile_id,
        host_id=_uid("host_test"),
        workspace="/tmp/omnigent-task-test",
    )
    assert profile_resp.status_code == 200


async def test_resolve_routes_event_and_bootstraps_manager(
    client: httpx.AsyncClient,
    manager_agent_profile_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    await _put_broker_profile(client, manager_agent_profile_id)
    event_id = _uid("event_resolve")
    task_event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build finished",
        state="awaiting_grouping",
    )
    create_resp = await client.post(
        "/v1/agent-tasks",
        json={"title": "Upload retries", "goal": "all uploads retry to success"},
    )
    task_id = create_resp.json()["id"]

    resolve_resp = await client.post(
        "/v1/task-events/batch-resolve",
        json={
            "event_ids": [event_id],
            "task_id": task_id,
        },
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    resolved = resolve_resp.json()["data"][0]
    assert resolved["state"] == "routed"
    assert resolved["task_id"] == task_id

    task_resp = await client.get(f"/v1/agent-tasks/{task_id}")
    assert task_resp.json()["manager_conversation_id"] is not None


async def test_batch_route_manager_success_and_same_manager_idempotence(
    client: httpx.AsyncClient,
    db_uri: str,
    manager_agent_profile_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    manager_id = _uid("route-manager")
    _register_manager(
        db_uri,
        conversation_id=manager_id,
        agent_id=manager_agent_profile_id,
    )
    event_id = _uid("route-manager-event")
    task_event_store.create_event(
        event_id,
        "build.failed",
        "Build failed",
        state="awaiting_grouping",
        owner_user_id="__anonymous__",
    )

    first = await client.post(
        "/v1/task-events/batch-route-manager",
        json={"event_ids": [event_id], "manager_conversation_id": manager_id},
    )
    assert first.status_code == 200, first.text
    routed = first.json()["data"][0]
    assert routed["state"] == "routed"
    assert routed["task_id"] is None
    assert routed["manager_conversation_id"] == manager_id

    second = await client.post(
        "/v1/task-events/batch-route-manager",
        json={"event_ids": [event_id], "manager_conversation_id": manager_id},
    )
    assert second.status_code == 200
    assert second.json()["data"][0] == routed


async def test_batch_route_manager_enforces_manager_and_event_owner(
    client: httpx.AsyncClient,
    db_uri: str,
    manager_agent_profile_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    other_manager_id = _uid("other-owner-manager")
    _register_manager(
        db_uri,
        conversation_id=other_manager_id,
        agent_id=manager_agent_profile_id,
        owner_user_id="someone-else",
    )
    mine_event_id = _uid("mine-event")
    task_event_store.create_event(
        mine_event_id,
        "build.failed",
        "Mine",
        state="awaiting_grouping",
        owner_user_id="__anonymous__",
    )
    manager_denied = await client.post(
        "/v1/task-events/batch-route-manager",
        json={
            "event_ids": [mine_event_id],
            "manager_conversation_id": other_manager_id,
        },
    )
    assert manager_denied.status_code == 404

    mine_manager_id = _uid("mine-manager")
    _register_manager(
        db_uri,
        conversation_id=mine_manager_id,
        agent_id=manager_agent_profile_id,
    )
    other_event_id = _uid("other-owner-event")
    task_event_store.create_event(
        other_event_id,
        "build.failed",
        "Theirs",
        state="awaiting_grouping",
        owner_user_id="someone-else",
    )
    event_denied = await client.post(
        "/v1/task-events/batch-route-manager",
        json={
            "event_ids": [other_event_id],
            "manager_conversation_id": mine_manager_id,
        },
    )
    assert event_denied.status_code == 404


async def test_batch_route_manager_rejects_invalid_state(
    client: httpx.AsyncClient,
    db_uri: str,
    manager_agent_profile_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    manager_id = _uid("invalid-state-manager")
    _register_manager(
        db_uri,
        conversation_id=manager_id,
        agent_id=manager_agent_profile_id,
    )
    event_id = _uid("reconciled-event")
    task_event_store.create_event(
        event_id,
        "build.finished",
        "Already reconciled",
        state="reconciled",
        owner_user_id="__anonymous__",
    )

    resp = await client.post(
        "/v1/task-events/batch-route-manager",
        json={"event_ids": [event_id], "manager_conversation_id": manager_id},
    )

    assert resp.status_code == 409


async def test_batch_route_manager_conflict_does_not_partially_route(
    client: httpx.AsyncClient,
    db_uri: str,
    manager_agent_profile_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    manager_id = _uid("atomic-manager")
    other_manager_id = _uid("atomic-other-manager")
    _register_manager(
        db_uri,
        conversation_id=manager_id,
        agent_id=manager_agent_profile_id,
    )
    routable_id = _uid("atomic-routable")
    conflict_id = _uid("atomic-conflict")
    task_event_store.create_event(
        routable_id,
        "build.failed",
        "Routable",
        state="awaiting_grouping",
        owner_user_id="__anonymous__",
    )
    task_event_store.create_event(
        conflict_id,
        "build.failed",
        "Already routed elsewhere",
        state="routed",
        manager_conversation_id=other_manager_id,
        owner_user_id="__anonymous__",
    )

    resp = await client.post(
        "/v1/task-events/batch-route-manager",
        json={
            "event_ids": [routable_id, conflict_id],
            "manager_conversation_id": manager_id,
        },
    )

    assert resp.status_code == 409
    untouched = task_event_store.get_event(routable_id)
    assert untouched is not None
    assert untouched.state == "awaiting_grouping"
    assert untouched.manager_conversation_id is None


async def test_batch_route_manager_rejects_host_mismatch(
    client: httpx.AsyncClient,
    db_uri: str,
    manager_agent_profile_id: str,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    manager_id = _uid("host-manager")
    _register_manager(
        db_uri,
        conversation_id=manager_id,
        agent_id=manager_agent_profile_id,
        host_id="manager-host",
    )
    event_id = _uid("other-host-event")
    task_event_store.create_event(
        event_id,
        "build.failed",
        "Other host",
        state="awaiting_grouping",
        owner_user_id="__anonymous__",
        source_offset="host:event-host",
    )

    resp = await client.post(
        "/v1/task-events/batch-route-manager",
        json={"event_ids": [event_id], "manager_conversation_id": manager_id},
    )

    assert resp.status_code == 409


async def test_dismiss_event(
    client: httpx.AsyncClient,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    event_id = _uid("event_dismiss")
    task_event_store.create_event(
        event_id=event_id,
        event_type="build.failed",
        title="Build failed",
        state="awaiting_grouping",
    )
    resp = await client.post(f"/v1/task-events/{event_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["state"] == "dismissed"


async def test_owned_routed_event_can_be_filed_as_fyi_or_dismissed(
    client: httpx.AsyncClient,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    fyi_event_id = _uid("manager-routed-fyi")
    dismiss_event_id = _uid("manager-routed-dismiss")
    for event_id in (fyi_event_id, dismiss_event_id):
        task_event_store.create_event(
            event_id,
            "build.finished",
            "No action needed",
            state="routed",
            manager_conversation_id=_uid("owned-manager"),
            owner_user_id="__anonymous__",
        )

    fyi = await client.post(
        "/v1/task-events/fyi-clusters",
        json={"event_ids": [fyi_event_id], "headline": "Informational build"},
    )
    dismissed = await client.post(f"/v1/task-events/{dismiss_event_id}/dismiss")

    assert fyi.status_code == 200, fyi.text
    assert task_event_store.get_event(fyi_event_id).state == "classified_fyi"
    assert dismissed.status_code == 200
    assert dismissed.json()["state"] == "dismissed"


async def test_event_handling_rejects_another_owner(
    client: httpx.AsyncClient,
    task_event_store: SqlAlchemyTaskEventStore,
) -> None:
    event_id = _uid("other-owner-routed-event")
    task_event_store.create_event(
        event_id,
        "build.finished",
        "Their event",
        state="routed",
        manager_conversation_id=_uid("their-manager"),
        owner_user_id="someone-else",
    )

    fyi = await client.post(
        "/v1/task-events/fyi-clusters",
        json={"event_ids": [event_id], "headline": "Not mine"},
    )
    dismissed = await client.post(f"/v1/task-events/{event_id}/dismiss")

    assert fyi.status_code == 404
    assert dismissed.status_code == 404
    assert task_event_store.get_event(event_id).state == "routed"


async def test_bootstrap_rejects_dead_manager_session(
    client: httpx.AsyncClient,
    manager_agent_profile_id: str,
    db_uri: str,
) -> None:
    await _put_broker_profile(client, manager_agent_profile_id)
    _seed_live_host(db_uri, "dead-session-host")
    dead_conversation_id = _uid("dead_conv")
    create_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Dead session task",
            "goal": "Reject stale manager sessions",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    task_id = create_resp.json()["id"]
    SqlAlchemyTaskStore(db_uri).update(
        task_id,
        manager_conversation_id=dead_conversation_id,
    )
    bootstrap_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/bootstrap",
        json={},
    )
    assert bootstrap_resp.status_code == 409


async def test_ambiguous_inbox_clusters_stalled_events(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """GET ambiguous-inbox groups stalled events and suggests task candidates."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    paused_id = _uid("ambiguous-paused")
    task_store.create(
        paused_id,
        "Upload retries",
        "uploads retry to success",
        state="pending",
        tags=[TaskTag(task_id=paused_id, tag_type="repo", tag="omnigent-fork")],
    )
    event_id = _uid("ambiguous-event")
    event_store.create_event(
        event_id,
        "build.finished",
        "Upload retries failed",
        state="awaiting_grouping",
        tags=[EventTag(tag_type="repo", tag="omnigent-fork")],
    )

    resp = await client.get("/v1/task-events/ambiguous-inbox")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "agent.task.ambiguous_inbox"
    cluster = body["clusters"][0]
    assert any(event["id"] == event_id for event in cluster["events"])
    assert cluster["suggested_candidates"][0]["task_id"] == paused_id


async def test_match_tasks_ranks_pending_tasks(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """POST match-tasks returns ranked active and pending task candidates."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    paused_id = _uid("match-paused-task")
    task_store.create(
        paused_id,
        "omnigent-fork",
        "uploads retry to success",
        state="pending",
        tags=[TaskTag(task_id=paused_id, tag_type="repo", tag="omnigent-fork")],
    )
    event_id = _uid("match-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        tags=[EventTag(tag_type="repo", tag="omnigent-fork")],
    )

    matched = await client.post(
        "/v1/task-events/match-tasks",
        json={"event_ids": [event_id]},
    )
    assert matched.status_code == 200
    candidates = matched.json()["candidates"]
    assert candidates[0]["task_id"] == paused_id
    assert candidates[0]["state"] == "pending"
