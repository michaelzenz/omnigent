"""Phase 4 route tests: task items, dispatch, dashboard, completion."""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import (
    TASK_MANAGER_AGENT_NAME,
    TASK_WORKER_AGENT_NAME,
    resolve_task_agent_id,
)
from omnigent.agent_tasks.completion import (
    TaskCompletionContext,
    configure_task_completion,
    notify_worker_session_status,
)
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore
from tests.server.routes.agent_task_api import put_agent_role_profile

WORKER_ROLE_SLUG = "investigator"
WORKER_ROLE_KEY = f"worker:{WORKER_ROLE_SLUG}"


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture(autouse=True)
def _patch_host_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip host liveness checks — route tests don't run a real host."""

    async def _skip_validation(*args: object, **kwargs: object) -> str | None:
        return kwargs.get("workspace")

    monkeypatch.setattr(
        "omnigent.server.routes.sessions._validate_session_workspace",
        _skip_validation,
    )

    from omnigent.server.routes._host_launch import HostLaunchTarget

    class _AutoResolveDict(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            if hasattr(value, "set_result") and not value.done():
                value.set_result({"status": "ok"})

    def _skip_launch(*args: object, **kwargs: object) -> HostLaunchTarget:
        host_id = kwargs.get("host_id", "")
        fake_conn = type(
            "FakeConn",
            (),
            {
                "host_id": host_id,
                "pending_launches": _AutoResolveDict(),
                "pending_stats": {},
            },
        )()
        return HostLaunchTarget(
            host=type("FakeHost", (), {"name": "test-host", "host_id": host_id})(),
            conn=fake_conn,
            conv=type("FakeConv", (), {"id": kwargs.get("session_id", "")})(),
        )

    monkeypatch.setattr(
        "omnigent.server.routes._host_launch.resolve_host_launch",
        _skip_launch,
    )

    from omnigent.server.host_registry import HostRegistry

    monkeypatch.setattr(HostRegistry, "send_text", staticmethod(lambda conn, data: None))


@pytest_asyncio.fixture()
async def task_manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


@pytest_asyncio.fixture()
async def worker_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_WORKER_AGENT_NAME)


@pytest_asyncio.fixture()
async def worker_role_key(client: httpx.AsyncClient, worker_agent_id: str, db_uri: str) -> str:
    """Register the custom worker role the phase 4 items are dispatched under."""
    host_id = _seed_live_host(db_uri, "worker-role-host")
    resp = await client.post(
        "/v1/agent-tasks/roles/worker",
        json={
            "slug": WORKER_ROLE_SLUG,
            "agent_profile_id": worker_agent_id,
            "host_id": host_id,
            "workspace": "/tmp/omnigent-worker",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["role"]


def _seed_live_host(db_uri: str, seed: str) -> str:
    host_id = _uid(seed)
    HostStore(db_uri).upsert_on_connect(host_id, seed, RESERVED_USER_LOCAL)
    return host_id


def _bootstrap_body() -> dict[str, str]:
    return {
        "workspace": "/tmp/omnigent-task-test",
        "harness": "cursor",
        "model": "composer-2.5",
    }


def _item_payload(worker_role_key: str) -> dict[str, str]:
    return {
        "title": "Investigate failure",
        "instructions": "Read logs and summarize the root cause.",
        "worker_role_key": worker_role_key,
    }


async def _bootstrapped_task(client: httpx.AsyncClient, db_uri: str) -> str:
    _seed_live_host(db_uri, "phase4-host")
    created = await client.post("/v1/agent-tasks", json={"title": "Phase 4 task"})
    task_id = created.json()["id"]
    bootstrap = await client.post(
        f"/v1/agent-tasks/{task_id}/bootstrap",
        json=_bootstrap_body(),
    )
    assert bootstrap.status_code == 200
    return task_id


async def test_dispatch_and_dashboard(
    client: httpx.AsyncClient,
    worker_role_key: str,
    db_uri: str,
) -> None:
    """Dispatch creates an execution visible on the task dashboard."""
    task_id = await _bootstrapped_task(client, db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("routed_event")
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build failed",
        task_id=task_id,
        state="routed",
    )

    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(worker_role_key),
            "state": "queued",
            "event_ids": [event_id],
        },
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]

    dispatch_resp = await client.post(
        f"/v1/task-items/{item_id}/dispatch",
        json=_bootstrap_body(),
    )
    assert dispatch_resp.status_code == 200
    body = dispatch_resp.json()
    assert body["status"] == "running"
    assert body["conversation_id"] is not None

    dashboard_resp = await client.get(f"/v1/agent-tasks/{task_id}/dashboard")
    assert dashboard_resp.status_code == 200
    dashboard = dashboard_resp.json()
    assert dashboard["derived"]["has_running_workers"] is True
    assert len(dashboard["workers"]) == 1
    assert dashboard["workers"][0]["kind"] == "managed"
    assert dashboard["workers"][0]["role_key"] == worker_role_key
    assert dashboard["workers"][0]["agent_profile_id"] is None
    assert dashboard["workers"][0]["executions"][0]["task_item_id"] == item_id


async def test_item_accept_enqueues_worker(
    client: httpx.AsyncClient,
    worker_role_key: str,
    db_uri: str,
) -> None:
    """Accepting a task item moves it to ``queued`` and enqueues a dispatch."""
    from omnigent.entities import AgentQueueKey
    from omnigent.stores.agent_queue_store.sqlalchemy_store import (
        SqlAlchemyAgentQueueStore,
    )

    task_id = await _bootstrapped_task(client, db_uri)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(worker_role_key),
            "submit_for_user_ack": True,
        },
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]
    assert item_resp.json()["state"] == "pending"

    resolve_resp = await client.post(
        f"/v1/task-items/{item_id}/resolve",
        json={"resolution": "accept_item", "edited_payload": _bootstrap_body()},
    )
    assert resolve_resp.status_code == 200
    resolved = resolve_resp.json()
    # Phase 4: no synchronous dispatch — the item is queued for the worker slot.
    assert resolved["state"] == "queued"
    assert resolved.get("execution_id") is None
    assert resolved.get("worker_conversation_id") is None

    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    items = queue_store.list_items(
        AgentQueueKey(role="worker", owner_user_id="__anonymous__", scope_id=resolved["worker_id"])
    )
    assert len(items) == 1
    assert items[0].kind == "item.dispatch"
    assert items[0].source_ids == [item_id]


async def test_item_edit_and_dispatch_enqueues(
    client: httpx.AsyncClient,
    worker_role_key: str,
    db_uri: str,
) -> None:
    """User-edited item payload is enqueued for dispatch (not launched)."""
    import json as _json

    from omnigent.entities import AgentQueueKey
    from omnigent.stores.agent_queue_store.sqlalchemy_store import (
        SqlAlchemyAgentQueueStore,
    )

    task_id = await _bootstrapped_task(client, db_uri)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(worker_role_key),
            "submit_for_user_ack": True,
        },
    )
    item_id = item_resp.json()["id"]
    resolve_resp = await client.post(
        f"/v1/task-items/{item_id}/resolve",
        json={
            "resolution": "edit_and_dispatch",
            "edited_payload": {
                "instructions": "Apply the patch and run unit tests only.",
                **_bootstrap_body(),
            },
        },
    )
    assert resolve_resp.status_code == 200
    resolved = resolve_resp.json()
    assert resolved["state"] == "queued"
    assert resolved.get("worker_conversation_id") is None
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    items = queue_store.list_items(
        AgentQueueKey(role="worker", owner_user_id="__anonymous__", scope_id=resolved["worker_id"])
    )
    assert len(items) == 1
    assert (
        "Apply the patch and run unit tests only." in _json.loads(items[0].payload)["instructions"]
    )


async def test_patch_queued_task_item(
    client: httpx.AsyncClient,
    worker_role_key: str,
    db_uri: str,
) -> None:
    """Queued work items can be edited before dispatch."""
    task_id = await _bootstrapped_task(client, db_uri)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(worker_role_key),
            "state": "queued",
        },
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]

    patch_resp = await client.patch(
        f"/v1/task-items/{item_id}",
        json={
            "title": "Updated title",
            "instructions": "Updated instructions",
        },
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["title"] == "Updated title"
    assert body["instructions"] == "Updated instructions"


async def test_worker_completion_hook(
    db_uri: str,
    task_manager_agent_id: str,
    worker_agent_id: str,
) -> None:
    """Worker idle status completes execution and wakes manager binding."""
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    task_id = _uid("task_complete")
    event_id = _uid("event_complete")
    task_item_id = _uid("item_complete")
    task_store.create(task_id, "Completion task")
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=task_manager_agent_id,
        host_id=_uid("host_mgr"),
        workspace="/tmp/mgr",
    )
    task_store.update(task_id, manager_conversation_id=manager_conv.id)
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Done",
        task_id=task_id,
        state="routed",
    )
    worker = worker_store.create_worker(
        _uid("worker_complete"),
        task_id,
        role_key=WORKER_ROLE_KEY,
    )
    item_store.create_item(
        task_item_id,
        task_id,
        "Completion item",
        state="running",
        worker_id=worker.id,
    )
    worker_conv = conversation_store.create_conversation(
        kind="sub_agent",
        title="Worker",
        parent_conversation_id=manager_conv.id,
        agent_id=worker_agent_id,
        host_id=_uid("host_worker"),
        workspace="/tmp/worker",
    )
    execution = event_store.create_execution(
        _uid("exec_complete"),
        task_item_id,
        task_id,
        status="running",
        conversation_id=worker_conv.id,
    )
    worker_store.update_worker(worker.id, session_id=worker_conv.id)

    configure_task_completion(
        TaskCompletionContext(
            task_store=task_store,
            task_event_store=event_store,
            task_item_store=item_store,
            conversation_store=conversation_store,
            worker_store=worker_store,
            runner_router=None,
        )
    )
    handled = await notify_worker_session_status(
        worker_conv.id,
        "idle",
        output="Root cause was a stale credential.",
    )
    assert handled is True
    updated = event_store.get_execution(execution.id)
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.result_summary == "Root cause was a stale credential."
    assert updated.task_item_id == task_item_id
    completed_item = item_store.get_item(task_item_id)
    assert completed_item is not None
    assert completed_item.state == "done"


async def test_activate_worker_lane_route(
    client: httpx.AsyncClient,
    worker_role_key: str,
    worker_agent_id: str,
    db_uri: str,
) -> None:
    """Activate starts a worker session before any item is dispatched."""
    task_id = await _bootstrapped_task(client, db_uri)
    _seed_live_host(db_uri, "activate-worker-host")
    profile_resp = await put_agent_role_profile(
        client,
        role=worker_role_key,
        agent_profile_id=worker_agent_id,
        host_id=_uid("activate-worker-host"),
        workspace="/tmp/omnigent-worker-activate",
    )
    assert profile_resp.status_code == 200

    worker_store = SqlAlchemyWorkerStore(db_uri)
    worker = worker_store.create_worker(_uid("activate-lane"), task_id, role_key=worker_role_key)

    activated = await client.post(f"/v1/task-workers/{worker.id}/activate")
    assert activated.status_code == 200, activated.text
    assert activated.json()["session_id"] is not None

    dashboard = await client.get(f"/v1/agent-tasks/{task_id}/dashboard")
    lane = next(w for w in dashboard.json()["workers"] if w["worker_id"] == worker.id)
    assert lane["session_id"] == activated.json()["session_id"]
