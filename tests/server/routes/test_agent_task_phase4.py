"""Phase 4 route tests: task items, dispatch, dashboard, completion."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
import pytest_asyncio

from omnigent.agent_tasks.completion import (
    TaskCompletionContext,
    configure_task_completion,
    notify_worker_session_status,
)
from omnigent.db.utils import generate_agent_id
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_provider_store.sqlalchemy_store import SqlAlchemyWorkerProviderStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture(autouse=True)
def _patch_host_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip host liveness checks — route tests don't run a real host."""

    from omnigent.server.routes._workspace_validation import WorkspaceValidationResult

    async def _skip_validation(*args: object, **kwargs: object) -> WorkspaceValidationResult:
        return WorkspaceValidationResult(canonical_path=kwargs.get("workspace") or "")

    monkeypatch.setattr(
        "omnigent.server.routes.sessions._validate_session_workspace",
        _skip_validation,
    )
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.orchestration._validate_session_workspace",
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
async def manager_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager", bundle_location="test:///bundle")
    return agent_id


@pytest_asyncio.fixture()
async def manager_id(
    client: httpx.AsyncClient,
    db_uri: str,
    manager_agent_id: str,
) -> str:
    """Register a first-class manager and return its durable id."""
    _seed_live_host(db_uri, "phase4-manager-host")
    manager_conversation_id = _uid("phase4-manager")
    SqlAlchemyConversationStore(db_uri).create_conversation(
        conversation_id=manager_conversation_id,
        title="Phase 4 manager",
        agent_id=manager_agent_id,
        host_id=_uid("phase4-manager-host"),
        workspace="/tmp/omnigent-task-test",
    )
    SqlAlchemyManagerStore(db_uri).upsert(
        manager_conversation_id,
        conversation_id=manager_conversation_id,
        owner_user_id="__anonymous__",
        role_key="manager:default",
        description="Owns phase 4 work.",
    )
    return manager_conversation_id


@pytest_asyncio.fixture()
async def worker_agent_id(db_uri: str) -> str:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-worker-agent", bundle_location="test:///bundle")
    return agent_id


@pytest_asyncio.fixture()
async def worker_provider_id(db_uri: str) -> str:
    """Register an internal Worker Provider backed by a loadable built-in agent."""
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore

    agent_store = SqlAlchemyAgentStore(db_uri)
    loadable = next(
        (
            a
            for a in agent_store.list().data
            if a.bundle_location and a.bundle_location != "test:///bundle"
        ),
        None,
    )
    assert loadable is not None, "no seeded built-in agent with a loadable bundle"
    provider_store = SqlAlchemyWorkerProviderStore(db_uri)
    provider_id = _uid("worker-provider")
    provider_store.create(
        provider_id,
        name="Phase4 worker provider",
        kind="internal",
        configuration=json.dumps({"agent_id": loadable.id}),
    )
    return provider_id


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


def _item_payload() -> dict[str, str]:
    return {
        "title": "Investigate failure",
        "instructions": "Read logs and summarize the root cause.",
    }


def _assignment(worker_provider_id: str) -> dict[str, str]:
    return {
        "provider_id": worker_provider_id,
        "host_id": _uid("phase4-worker-host"),
        "workspace": "/tmp/omnigent-worker",
    }


async def _assign_worker(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    item_id: str,
    worker_provider_id: str,
) -> str:
    """Assign a provider-backed worker to an item; return the worker id."""
    assigned = await client.post(
        f"/v1/agent-tasks/{task_id}/workers/assign",
        json={"assignments": [{"item_id": item_id, **_assignment(worker_provider_id)}]},
    )
    assert assigned.status_code == 200, assigned.text
    return assigned.json()["data"][0]["worker_id"]


async def _initialize_worker(client: httpx.AsyncClient, db_uri: str, worker_id: str) -> None:
    """Initialize a worker via the route and wait for the background spawn."""
    initialized = await client.post(f"/v1/task-workers/{worker_id}/initialize")
    assert initialized.status_code == 202, initialized.text
    worker_store = SqlAlchemyWorkerStore(db_uri)
    for _ in range(50):
        worker = worker_store.get_worker(worker_id)
        assert worker is not None
        if worker.state == "idle" and worker.target_id:
            return
        await asyncio.sleep(0.1)
    assert worker.state == "idle", worker.failure_reason


async def _bootstrapped_task(client: httpx.AsyncClient, db_uri: str, manager_id: str) -> str:
    _seed_live_host(db_uri, "phase4-host")
    created = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Phase 4 task",
            "goal": "complete phase 4",
            "state": "active",
            "manager_id": manager_id,
        },
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    bootstrap = await client.post(
        f"/v1/agent-tasks/{task_id}/bootstrap",
        json=_bootstrap_body(),
    )
    assert bootstrap.status_code == 200
    return task_id


async def test_dispatch_and_dashboard(
    client: httpx.AsyncClient,
    worker_provider_id: str,
    manager_id: str,
    db_uri: str,
) -> None:
    """Dispatch creates an execution visible on the task dashboard."""
    task_id = await _bootstrapped_task(client, db_uri, manager_id)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    # Mirror the stored task's owner/manager on the event — the item-create
    # claim check requires exact matches.
    stored_task = SqlAlchemyTaskStore(db_uri).get(task_id)
    assert stored_task is not None
    event_id = _uid("routed_event")
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build failed",
        task_id=task_id,
        manager_id=stored_task.manager_id,
        state="routed",
        owner_user_id=stored_task.owner_user_id,
    )

    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(),
            "event_ids": [event_id],
        },
    )
    assert item_resp.status_code == 200, item_resp.text
    item_id = item_resp.json()["id"]
    worker_id = await _assign_worker(
        client,
        task_id=task_id,
        item_id=item_id,
        worker_provider_id=worker_provider_id,
    )
    await _initialize_worker(client, db_uri, worker_id)

    dispatch_resp = await client.post(
        f"/v1/task-items/{item_id}/dispatch",
        json=_bootstrap_body(),
    )
    assert dispatch_resp.status_code == 200, dispatch_resp.text
    body = dispatch_resp.json()
    assert body["status"] == "running"
    assert body["conversation_id"] is not None

    dashboard_resp = await client.get(f"/v1/agent-tasks/{task_id}/dashboard")
    assert dashboard_resp.status_code == 200
    dashboard = dashboard_resp.json()
    assert dashboard["derived"]["has_running_workers"] is True
    assert len(dashboard["workers"]) == 1
    assert dashboard["workers"][0]["kind"] == "managed"
    assert dashboard["workers"][0]["worker_id"] == worker_id
    assert dashboard["workers"][0]["executions"][0]["task_item_id"] == item_id


async def test_item_accept_enqueues_worker(
    client: httpx.AsyncClient,
    worker_provider_id: str,
    manager_id: str,
    db_uri: str,
) -> None:
    """Accepting a task item moves it to ``queued`` and enqueues a dispatch."""
    from omnigent.entities import AgentQueueKey
    from omnigent.stores.agent_queue_store.sqlalchemy_store import (
        SqlAlchemyAgentQueueStore,
    )

    task_id = await _bootstrapped_task(client, db_uri, manager_id)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(),
            "submit_for_user_ack": True,
        },
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]
    assert item_resp.json()["state"] == "pending"
    worker_id = await _assign_worker(
        client,
        task_id=task_id,
        item_id=item_id,
        worker_provider_id=worker_provider_id,
    )

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
        AgentQueueKey(role="worker", owner_user_id="__anonymous__", scope_id=worker_id)
    )
    assert len(items) == 1
    assert items[0].kind == "item.dispatch"
    assert items[0].source_ids == [item_id]


async def test_item_edit_and_dispatch_enqueues(
    client: httpx.AsyncClient,
    worker_provider_id: str,
    manager_id: str,
    db_uri: str,
) -> None:
    """User-edited item payload is enqueued for dispatch (not launched)."""
    import json as _json

    from omnigent.entities import AgentQueueKey
    from omnigent.stores.agent_queue_store.sqlalchemy_store import (
        SqlAlchemyAgentQueueStore,
    )

    task_id = await _bootstrapped_task(client, db_uri, manager_id)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(),
            "submit_for_user_ack": True,
        },
    )
    item_id = item_resp.json()["id"]
    worker_id = await _assign_worker(
        client,
        task_id=task_id,
        item_id=item_id,
        worker_provider_id=worker_provider_id,
    )
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
        AgentQueueKey(role="worker", owner_user_id="__anonymous__", scope_id=worker_id)
    )
    assert len(items) == 1
    assert (
        "Apply the patch and run unit tests only." in _json.loads(items[0].payload)["instructions"]
    )


async def test_patch_queued_task_item(
    client: httpx.AsyncClient,
    worker_provider_id: str,
    manager_id: str,
    db_uri: str,
) -> None:
    """Queued work items can be edited before dispatch, under an edit lease."""
    task_id = await _bootstrapped_task(client, db_uri, manager_id)
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={
            **_item_payload(),
            "submit_for_user_ack": True,
        },
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]
    await _assign_worker(
        client,
        task_id=task_id,
        item_id=item_id,
        worker_provider_id=worker_provider_id,
    )
    accepted = await client.post(
        f"/v1/task-items/{item_id}/resolve",
        json={"resolution": "accept_item", "edited_payload": _bootstrap_body()},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "queued"

    # Instruction edits on a queued item hold an edit lease first.
    lease_token = _uid("edit-lease")
    lease_resp = await client.post(
        f"/v1/task-items/{item_id}/edit-lease",
        json={"token": lease_token},
    )
    assert lease_resp.status_code == 200, lease_resp.text

    patch_resp = await client.patch(
        f"/v1/task-items/{item_id}",
        json={
            "title": "Updated title",
            "instructions": "Updated instructions",
            "edit_lease_token": lease_token,
        },
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["title"] == "Updated title"
    assert body["instructions"] == "Updated instructions"


async def test_worker_completion_hook(
    db_uri: str,
    manager_agent_id: str,
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
    task_store.create(task_id, "Completion task", "complete the task")
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_agent_id,
        host_id=_uid("host_mgr"),
        workspace="/tmp/mgr",
    )
    task_store.update(task_id, manager_id=manager_conv.id)
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Done",
        task_id=task_id,
        state="routed",
    )
    worker = worker_store.create_worker(_uid("worker_complete"), task_id)
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
    worker_store.update_worker(worker.id, target_id=worker_conv.id)

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


async def test_initialize_worker_route(
    client: httpx.AsyncClient,
    worker_provider_id: str,
    worker_agent_id: str,
    manager_id: str,
    db_uri: str,
) -> None:
    """Initialize starts a worker session before any item is dispatched."""
    task_id = await _bootstrapped_task(client, db_uri, manager_id)
    _seed_live_host(db_uri, "initialize-worker-host")

    created = await client.post(
        f"/v1/agent-tasks/{task_id}/workers",
        json={
            "provider_id": worker_provider_id,
            "host_id": _uid("initialize-worker-host"),
            "workspace": "/tmp/omnigent-worker-init",
        },
    )
    assert created.status_code == 200, created.text
    worker_id = created.json()["worker_id"]

    initialized = await client.post(f"/v1/task-workers/{worker_id}/initialize")
    assert initialized.status_code == 202, initialized.text

    # Initialization runs in a background task — poll until the worker is idle.
    for _ in range(50):
        worker = SqlAlchemyWorkerStore(db_uri).get_worker(worker_id)
        assert worker is not None
        if worker.state == "idle" and worker.target_id:
            break
        await asyncio.sleep(0.1)
    assert worker.state == "idle", worker.failure_reason
    assert worker.target_id is not None

    dashboard = await client.get(f"/v1/agent-tasks/{task_id}/dashboard")
    lane = next(w for w in dashboard.json()["workers"] if w["worker_id"] == worker_id)
    assert lane["target_id"] == worker.target_id
