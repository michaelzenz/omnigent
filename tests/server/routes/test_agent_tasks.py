"""Tests for managed agent task routes (``/v1/agent-tasks``)."""

from __future__ import annotations

import uuid
from urllib.parse import quote

import httpx
import pytest
import pytest_asyncio

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_MANAGER_AGENT_NAME,
    TASK_SECRETARY_AGENT_NAME,
    TASK_SECRETARY_ROLE,
    resolve_task_agent_id,
)
from omnigent.agent_tasks.broker_session import NO_HOST_AVAILABLE_MESSAGE
from omnigent.db.utils import generate_agent_id
from omnigent.entities import EventTag
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER, token_bound_runner_id
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore
from omnigent.tools.builtins.puppygarden_api import PUPPYGARDEN_CALLER_CONVERSATION_HEADER
from tests.server.routes.agent_task_api import (
    agent_role_profile_url,
    agent_role_session_reset_url,
    agent_role_session_url,
    put_agent_role_profile,
    task_worker_url,
)


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _patch_workspace_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip host liveness + workspace stat checks for role-session unit tests.

    The full ``POST /v1/sessions`` path validates the workspace against a
    live host connection and launches a runner; role-session tests don't
    stand up a real host, so patch ``_validate_session_workspace``,
    ``resolve_host_launch``, and ``HostRegistry.send_text`` to skip the
    live-host requirement.
    """

    async def _skip_validation(*args: object, **kwargs: object) -> str | None:
        return kwargs.get("workspace")

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
        """Dict that auto-resolves any future stored in it with a success result."""

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
            conn=fake_conn,  # type: ignore[arg-type]
            conv=type("FakeConv", (), {"id": kwargs.get("session_id", "")})(),
        )

    monkeypatch.setattr(
        "omnigent.server.routes._host_launch.resolve_host_launch",
        _skip_launch,
    )

    # HostRegistry.send_text raises ConnectionError for unknown/replaced
    # connections; patch it to a no-op so the launch-frame send succeeds.
    from omnigent.server.host_registry import HostRegistry

    monkeypatch.setattr(HostRegistry, "send_text", staticmethod(lambda conn, data: None))


@pytest_asyncio.fixture()
async def task_manager_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    """Return the seeded task-manager built-in agent id."""
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_MANAGER_AGENT_NAME)


@pytest_asyncio.fixture()
async def secretary_agent_id(client: httpx.AsyncClient, db_uri: str) -> str:
    """Return the seeded task-secretary built-in agent id."""
    del client
    return resolve_task_agent_id(SqlAlchemyAgentStore(db_uri), TASK_SECRETARY_AGENT_NAME)


@pytest_asyncio.fixture()
async def custom_agent_id(db_uri: str) -> str:
    """Register an agent that is none of the packaged task built-ins."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="custom-agent", bundle_location="test:///bundle")
    return agent_id


def _create_payload(**overrides: object) -> dict:
    base: dict = {
        "title": "S3 upload reliability",
        "goal": "All S3 uploads eventually succeed without manual retry",
        "internal_note": "retry flaky uploads",
        "tags": [{"tag_type": "domain", "tag": "s3"}],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


async def test_create_and_get_task(client: httpx.AsyncClient) -> None:
    """Creating a task returns the task snapshot; GET includes tags."""
    create_resp = await client.post("/v1/agent-tasks", json=_create_payload())
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["object"] == "agent.task"
    assert created["state"] == "pending"
    assert created["goal"] == "All S3 uploads eventually succeed without manual retry"
    assert created["manager_role_key"] == "manager:default"
    assert created["worker_role_key"] == "worker:default"
    # The agent behind each lane is named by the role, not by the task.
    assert "agent_profile_id" not in created
    assert created["tags"] == [{"tag_type": "domain", "tag": "s3"}]

    get_resp = await client.get(f"/v1/agent-tasks/{created['id']}")
    assert get_resp.status_code == 200
    loaded = get_resp.json()
    assert loaded["id"] == created["id"]
    assert loaded["title"] == "S3 upload reliability"
    assert loaded["tags"] == created["tags"]


async def test_create_task_requires_goal_and_supported_state(
    client: httpx.AsyncClient,
) -> None:
    missing_goal = await client.post(
        "/v1/agent-tasks",
        json={"title": "Missing goal"},
    )
    assert missing_goal.status_code == 422

    invalid_state = await client.post(
        "/v1/agent-tasks",
        json={"title": "Invalid state", "goal": "Never created", "state": "idle"},
    )
    assert invalid_state.status_code == 422

    manager_session = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Invalid manager session",
            "goal": "Never created",
            "manager_conversation_id": _uid("manager"),
        },
    )
    assert manager_session.status_code == 422


async def test_create_defaults_manager_role_to_task_manager_agent(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    """A task with no role keys runs the built-in task-manager through manager:default."""
    _seed_live_host(db_uri, "default-manager-host")
    create_resp = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Default manager task",
            "goal": "default manager task done",
            "tags": [{"tag_type": "domain", "tag": "s3"}],
        },
    )
    assert create_resp.status_code == 200
    assert create_resp.json()["manager_role_key"] == "manager:default"

    profile_resp = await client.get(agent_role_profile_url("manager:default"))
    assert profile_resp.status_code == 200
    # manager:default auto-forks from the packaged task-manager on first
    # load, so the role owns its profile (decoupled from the reseeded built-in).
    assert profile_resp.json()["agent_profile_id"] != task_manager_agent_id
    assert profile_resp.json()["agent_name"].startswith("task-manager-fork-")


async def test_create_active_task_bootstraps_manager(
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_validation(monkeypatch)
    _seed_live_host(db_uri, "active-task-host")
    created = await client.post(
        "/v1/agent-tasks",
        json={
            "title": "Active task",
            "goal": "Manager session is running",
            "state": "active",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["state"] == "active"
    assert created.json()["manager_conversation_id"] is not None


async def test_role_profile_rejects_missing_agent_profile(client: httpx.AsyncClient) -> None:
    """Pointing a role at an unknown agent_profile_id returns 404."""
    resp = await put_agent_role_profile(
        client,
        role=TASK_BROKER_ROLE,
        agent_profile_id=_uid("missing_profile"),
        host_id=_uid("missing_profile_host"),
        workspace="/tmp/broker",
    )
    assert resp.status_code == 404


async def test_list_tasks_filters_by_state(client: httpx.AsyncClient) -> None:
    """List endpoint filters by state query param."""
    pending_task = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(title="Pending task"),
    )
    archived = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(title="Archived task"),
    )
    await client.delete(f"/v1/agent-tasks/{archived.json()['id']}")

    list_resp = await client.get("/v1/agent-tasks?state=pending")
    assert list_resp.status_code == 200
    ids = {row["id"] for row in list_resp.json()["data"]}
    assert pending_task.json()["id"] in ids
    assert archived.json()["id"] not in ids


async def test_patch_task(client: httpx.AsyncClient) -> None:
    """PATCH updates mutable fields."""
    created = (await client.post("/v1/agent-tasks", json=_create_payload())).json()
    patch_resp = await client.patch(
        f"/v1/agent-tasks/{created['id']}",
        json={"title": "Renamed task", "state": "pending"},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["title"] == "Renamed task"
    assert body["state"] == "pending"


async def test_put_tags_replaces_all(client: httpx.AsyncClient) -> None:
    """PUT /tags replaces the full tag set."""
    created = (await client.post("/v1/agent-tasks", json=_create_payload())).json()
    put_resp = await client.put(
        f"/v1/agent-tasks/{created['id']}/tags",
        json={
            "tags": [
                {"tag_type": "component", "tag": "build"},
                {"tag_type": "domain", "tag": "ci"},
            ]
        },
    )
    assert put_resp.status_code == 200
    tags = put_resp.json()["tags"]
    assert sorted(tags, key=lambda row: row["tag"]) == [
        {"tag_type": "component", "tag": "build"},
        {"tag_type": "domain", "tag": "ci"},
    ]


async def test_list_executions(client: httpx.AsyncClient, db_uri: str) -> None:
    """Execution history is exposed for a task."""
    created = (await client.post("/v1/agent-tasks", json=_create_payload())).json()
    task_id = created["id"]
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    event_id = _uid("event_exec")
    task_item_id = _uid("item_exec")
    event_store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build passed",
        task_id=task_id,
        tags=[EventTag(tag_type="domain", tag="ci")],
    )
    item_store.create_item(
        task_item_id,
        task_id,
        "Investigate build",
        state="running",
    )
    execution_id = _uid("execution_1")
    event_store.create_execution(
        execution_id=execution_id,
        task_item_id=task_item_id,
        task_id=task_id,
        status="succeeded",
    )
    event_store.update_execution(execution_id, status="succeeded", result_summary="done")

    resp = await client.get(f"/v1/agent-tasks/{task_id}/executions")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == execution_id
    assert rows[0]["status"] == "succeeded"


async def test_delete_archives_task(client: httpx.AsyncClient) -> None:
    """DELETE soft-archives the task."""
    created = (await client.post("/v1/agent-tasks", json=_create_payload())).json()
    delete_resp = await client.delete(f"/v1/agent-tasks/{created['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True
    assert delete_resp.json()["state"] == "archived"

    get_resp = await client.get(f"/v1/agent-tasks/{created['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["state"] == "archived"


async def test_permanent_delete_without_archive(client: httpx.AsyncClient) -> None:
    """Permanent delete works on a pending task without archiving first."""
    created = (await client.post("/v1/agent-tasks", json=_create_payload())).json()
    task_id = created["id"]
    assert created["state"] == "pending"

    perm_resp = await client.delete(f"/v1/agent-tasks/{task_id}/permanent")
    assert perm_resp.status_code == 200
    assert perm_resp.json()["deleted"] is True
    assert perm_resp.json()["permanent"] is True

    get_resp = await client.get(f"/v1/agent-tasks/{task_id}")
    assert get_resp.status_code == 404


async def test_unknown_task_agent_role_returns_404(client: httpx.AsyncClient) -> None:
    profile_resp = await client.get(agent_role_profile_url("manager"))
    assert profile_resp.status_code == 404


async def test_broker_profile_round_trip(
    client: httpx.AsyncClient,
    custom_agent_id: str,
) -> None:
    """Broker role accepts and stores a profile independent of secretary."""
    profile_resp = await put_agent_role_profile(
        client,
        role=TASK_BROKER_ROLE,
        agent_profile_id=custom_agent_id,
        host_id=_uid("broker_host"),
        workspace="/tmp/broker",
    )
    assert profile_resp.status_code == 200
    body = profile_resp.json()
    assert body["role"] == TASK_BROKER_ROLE
    assert body["kind"] == "broker"
    assert body["agent_profile_id"] == custom_agent_id

    loaded = await client.get(agent_role_profile_url(TASK_BROKER_ROLE))
    assert loaded.status_code == 200
    assert loaded.json()["workspace"] == "/tmp/broker"
    assert loaded.json()["agent_profile_id"] == custom_agent_id
    # Definitions are shared; only the live session is per user.
    assert loaded.json()["conversation_id"] is None


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
    role_key: str = "manager:default",
    description: str = "Owns upload reliability.",
    parent_conversation_id: str | None = None,
    tunnel_token: str | None = None,
) -> None:
    SqlAlchemyConversationStore(db_uri).create_conversation(
        conversation_id=conversation_id,
        title="Upload manager",
        parent_conversation_id=parent_conversation_id,
        agent_id=agent_id,
        runner_id=token_bound_runner_id(tunnel_token) if tunnel_token else None,
        host_id=_uid("manager-api-host"),
        workspace="/tmp/manager-api",
    )
    SqlAlchemyManagerStore(db_uri).upsert(
        conversation_id,
        owner_user_id=owner_user_id,
        role_key=role_key,
        description=description,
    )


async def test_list_managers_includes_zero_task_manager_metadata(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    conversation_id = _uid("zero-task-manager")
    _register_manager(
        db_uri,
        conversation_id=conversation_id,
        agent_id=task_manager_agent_id,
        role_key="manager:uploads",
        description="Owns all upload workflows.",
    )

    resp = await client.get("/v1/agent-tasks/managers")

    assert resp.status_code == 200
    manager = next(
        row for row in resp.json()["managers"] if row["conversation_id"] == conversation_id
    )
    assert manager["description"] == "Owns all upload workflows."
    assert manager["role_key"] == "manager:uploads"
    assert manager["capacity"] > 0
    assert manager["task_count"] == 0
    assert manager["tasks"] == []


async def test_create_manager_registers_top_level_manager_role(
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_validation(monkeypatch)
    _seed_live_host(db_uri, "create-manager-host")
    profile = await client.get(agent_role_profile_url("manager:default"))
    assert profile.status_code == 200

    resp = await client.post(
        "/v1/agent-tasks/managers",
        json={
            "role_key": "manager:default",
            "description": "Owns release readiness.",
            "title": "Release manager",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role_key"] == "manager:default"
    assert body["description"] == "Owns release readiness."
    assert body["task_count"] == 0
    conversation = SqlAlchemyConversationStore(db_uri).get_conversation(
        body["conversation_id"]
    )
    assert conversation is not None
    assert conversation.parent_conversation_id is None
    stored = SqlAlchemyManagerStore(db_uri).get(body["conversation_id"])
    assert stored is not None
    assert stored.owner_user_id == "__anonymous__"
    assert stored.role_key == "manager:default"


async def test_update_manager_self_updates_only_owned_caller(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    tunnel_token = "manager-self-token"
    caller_id = _uid("caller-manager")
    other_id = _uid("other-manager")
    _register_manager(
        db_uri,
        conversation_id=caller_id,
        agent_id=task_manager_agent_id,
        description="Caller old scope.",
        tunnel_token=tunnel_token,
    )
    _register_manager(
        db_uri,
        conversation_id=other_id,
        agent_id=task_manager_agent_id,
        description="Other scope.",
    )

    resp = await client.patch(
        "/v1/agent-tasks/managers/self",
        headers={
            PUPPYGARDEN_CALLER_CONVERSATION_HEADER: caller_id,
            RUNNER_TUNNEL_TOKEN_HEADER: tunnel_token,
        },
        json={"description": "Caller new scope."},
    )

    assert resp.status_code == 200
    assert resp.json()["conversation_id"] == caller_id
    assert resp.json()["description"] == "Caller new scope."
    store = SqlAlchemyManagerStore(db_uri)
    caller = store.get(caller_id)
    other = store.get(other_id)
    assert caller is not None
    assert other is not None
    assert caller.description == "Caller new scope."
    assert other.description == "Other scope."


async def test_update_manager_self_rejects_missing_spoofed_and_cross_owner_identity(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    missing = await client.patch(
        "/v1/agent-tasks/managers/self",
        json={"description": "No caller."},
    )
    assert missing.status_code == 401

    spoofed = await client.patch(
        "/v1/agent-tasks/managers/self",
        headers={
            PUPPYGARDEN_CALLER_CONVERSATION_HEADER: _uid("unknown-manager"),
            RUNNER_TUNNEL_TOKEN_HEADER: "unknown-token",
        },
        json={"description": "Spoofed caller."},
    )
    assert spoofed.status_code == 403

    bound_id = _uid("token-bound-manager")
    _register_manager(
        db_uri,
        conversation_id=bound_id,
        agent_id=task_manager_agent_id,
        tunnel_token="correct-token",
    )
    wrong_token = await client.patch(
        "/v1/agent-tasks/managers/self",
        headers={
            PUPPYGARDEN_CALLER_CONVERSATION_HEADER: bound_id,
            RUNNER_TUNNEL_TOKEN_HEADER: "wrong-token",
        },
        json={"description": "Wrong runner."},
    )
    assert wrong_token.status_code == 403

    cross_owner_id = _uid("cross-owner-manager")
    _register_manager(
        db_uri,
        conversation_id=cross_owner_id,
        agent_id=task_manager_agent_id,
        owner_user_id="someone-else",
        tunnel_token="cross-owner-token",
    )
    cross_owner = await client.patch(
        "/v1/agent-tasks/managers/self",
        headers={
            PUPPYGARDEN_CALLER_CONVERSATION_HEADER: cross_owner_id,
            RUNNER_TUNNEL_TOKEN_HEADER: "cross-owner-token",
        },
        json={"description": "Cross-owner caller."},
    )
    assert cross_owner.status_code == 403

    parent_id = _uid("parent-manager")
    SqlAlchemyConversationStore(db_uri).create_conversation(
        conversation_id=parent_id,
        title="Parent",
        agent_id=task_manager_agent_id,
    )
    child_id = _uid("child-manager")
    _register_manager(
        db_uri,
        conversation_id=child_id,
        agent_id=task_manager_agent_id,
        parent_conversation_id=parent_id,
        tunnel_token="child-token",
    )
    child = await client.patch(
        "/v1/agent-tasks/managers/self",
        headers={
            PUPPYGARDEN_CALLER_CONVERSATION_HEADER: child_id,
            RUNNER_TUNNEL_TOKEN_HEADER: "child-token",
        },
        json={"description": "Child caller."},
    )
    assert child.status_code == 403


async def test_task_bindings_reject_foreign_first_class_manager(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    foreign_manager_id = _uid("foreign-binding-manager")
    _register_manager(
        db_uri,
        conversation_id=foreign_manager_id,
        agent_id=task_manager_agent_id,
        owner_user_id="someone-else",
    )
    created = await client.post(
        "/v1/agent-tasks",
        json={"title": "Owned task", "goal": "Stay owner-isolated"},
    )
    assert created.status_code == 200

    patched = await client.patch(
        f"/v1/agent-tasks/{created.json()['id']}",
        json={"manager_conversation_id": foreign_manager_id},
    )
    assert patched.status_code == 404

    packaged = await client.post(
        "/v1/agent-tasks/packages",
        json={
            "title": "Foreign package",
            "goal": "Must not bind",
            "manager_conversation_id": foreign_manager_id,
            "items": [
                {
                    "title": "Rejected item",
                    "event_ids": [_uid("foreign-package-event")],
                }
            ],
        },
    )
    assert packaged.status_code == 404


async def test_ack_manager_routed_event_assigns_task_and_preserves_manager(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    manager_id = _uid("reconcile-manager")
    _register_manager(
        db_uri,
        conversation_id=manager_id,
        agent_id=task_manager_agent_id,
    )
    task_id = _uid("reconcile-task")
    SqlAlchemyTaskStore(db_uri).create(
        task_id,
        "Reconcile task",
        "Assign the routed event",
        owner_user_id="__anonymous__",
        manager_conversation_id=manager_id,
    )
    event_id = _uid("manager-routed-reconcile-event")
    SqlAlchemyTaskEventStore(db_uri).create_event(
        event_id,
        "build.finished",
        "Build completed",
        manager_conversation_id=manager_id,
        state="routed",
        owner_user_id="__anonymous__",
    )

    resp = await client.post(
        f"/v1/agent-tasks/{task_id}/ack",
        json={"event_ids": [event_id]},
    )

    assert resp.status_code == 200
    event = resp.json()["data"][0]
    assert event["state"] == "reconciled"
    assert event["task_id"] == task_id
    assert event["manager_conversation_id"] == manager_id


async def test_list_role_profiles_includes_system_roles(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    _seed_live_host(db_uri, "list-profiles-host")
    list_resp = await client.get("/v1/agent-tasks/roles/profiles")
    assert list_resp.status_code == 200
    roles = {row["role"] for row in list_resp.json()["data"]}
    assert "broker" in roles
    assert "secretary" in roles
    assert "manager:default" in roles
    assert "worker:default" in roles


async def test_list_role_profiles_kind_filter(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """kind filters by role family (broker/secretary/manager/worker)."""
    _seed_live_host(db_uri, "kind-filter-host")
    workers = await client.get("/v1/agent-tasks/roles/profiles?kind=worker")
    assert workers.status_code == 200
    worker_roles = {row["role"] for row in workers.json()["data"]}
    assert "worker:default" in worker_roles
    assert "broker" not in worker_roles
    assert "secretary" not in worker_roles
    assert "manager:default" not in worker_roles

    managers = await client.get("/v1/agent-tasks/roles/profiles?kind=manager")
    manager_roles = {row["role"] for row in managers.json()["data"]}
    assert "manager:default" in manager_roles
    assert "worker:default" not in manager_roles


async def test_role_profile_description_round_trip(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Description seeds from packaged defaults and round-trips via PUT."""
    _seed_live_host(db_uri, "desc-host")
    # Packaged worker:default seeds a default description on first read.
    get_resp = await client.get("/v1/agent-tasks/roles/worker:default/profile")
    assert get_resp.status_code == 200
    seeded = get_resp.json()
    assert seeded["description"] is not None
    assert "general-purpose" in seeded["description"].lower()

    # PUT updates the description and persists.
    put_resp = await client.put(
        "/v1/agent-tasks/roles/worker:default/profile",
        json={
            "agent_profile_id": seeded["agent_profile_id"],
            "description": "Reviews pull requests for API correctness.",
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["description"] == "Reviews pull requests for API correctness."

    # An empty string clears the description back to null.
    clear_resp = await client.put(
        "/v1/agent-tasks/roles/worker:default/profile",
        json={"agent_profile_id": seeded["agent_profile_id"], "description": ""},
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["description"] is None

    # The listing surfaces the description so the manager can pick a lane.
    list_resp = await client.get("/v1/agent-tasks/roles/profiles?kind=worker")
    assert list_resp.status_code == 200
    row = next(r for r in list_resp.json()["data"] if r["role"] == "worker:default")
    assert row["description"] is None


async def test_create_custom_worker_role_seeds_description(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    """A custom worker role inherits the default worker description, overridable on creation."""
    _seed_live_host(db_uri, "custom-desc-host")
    create_resp = await client.post(
        "/v1/agent-tasks/roles/worker",
        json={"slug": "reviewer", "agent_profile_id": task_manager_agent_id},
    )
    assert create_resp.status_code == 200
    assert create_resp.json()["role"] == "worker:reviewer"
    # Inherits the packaged worker:default description via the fallback.
    assert create_resp.json()["description"] is not None

    # Setting a description on creation overrides the inherited default.
    create_with_desc = await client.post(
        "/v1/agent-tasks/roles/worker",
        json={
            "slug": "coder",
            "agent_profile_id": task_manager_agent_id,
            "description": "Implements coding task items.",
        },
    )
    assert create_with_desc.status_code == 200
    assert create_with_desc.json()["description"] == "Implements coding task items."


async def test_role_profile_returns_candidate_agents(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Profile response lists the packaged agents backing the role's kind."""
    _seed_live_host(db_uri, "candidate-host")
    resp = await client.get("/v1/agent-tasks/roles/worker:default/profile")
    assert resp.status_code == 200
    body = resp.json()
    names = {c["name"] for c in body["candidate_agents"]}
    assert {"default-worker", "coding-agent"}.issubset(names)
    # every candidate is flagged packaged for the import-button gating
    assert all(c["packaged"] for c in body["candidate_agents"])


async def test_import_role_agent_forks_and_rebinds(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Import forks a packaged worker agent into a private is_role copy."""
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore

    _seed_live_host(db_uri, "import-host")
    store = SqlAlchemyAgentStore(db_uri)
    default_worker = store.get_by_name("default-worker")
    assert default_worker is not None

    resp = await client.post(
        "/v1/agent-tasks/roles/worker:default/import-agent",
        json={"agent_id": default_worker.id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    new_id = body["agent_profile_id"]
    assert new_id != default_worker.id
    assert body["agent_name"].startswith("default-worker-fork-")

    fork = store.get(new_id)
    assert fork is not None
    assert fork.is_role is True
    # the fork is hidden from the public catalog but resolvable by id
    listed_ids = {a.id for a in store.list().data}
    assert new_id not in listed_ids

    # the bound fork is NOT offered as a candidate (you can't re-import what's
    # already bound); only the packaged sources remain in the dropdown
    candidate_ids = {c["id"] for c in body["candidate_agents"]}
    assert new_id not in candidate_ids
    assert default_worker.id in candidate_ids


async def test_update_role_prompt_auto_forks_then_edits_in_place(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Setting a prompt on a packaged-bound role auto-forks; a second set edits in place."""
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore

    _seed_live_host(db_uri, "prompt-host")
    store = SqlAlchemyAgentStore(db_uri)
    default_worker = store.get_by_name("default-worker")
    assert default_worker is not None

    # worker:default is auto-forked from the packaged default-worker on
    # first load (via _load_role_profile), so the prompt endpoint edits the
    # bound fork in place.
    first = await client.put(
        "/v1/agent-tasks/roles/worker:default/prompt",
        json={"prompt": "You are a careful reviewer."},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    fork_id = first_body["agent_profile_id"]
    assert fork_id != default_worker.id
    assert first_body["prompt"] == "You are a careful reviewer."
    fork = store.get(fork_id)
    assert fork is not None and fork.is_role is True

    # second edit stays on the same fork (in place, no rebind)
    second = await client.put(
        "/v1/agent-tasks/roles/worker:default/prompt",
        json={"prompt": "You are a careful reviewer. Be concise."},
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["agent_profile_id"] == fork_id
    assert second_body["prompt"] == "You are a careful reviewer. Be concise."


async def test_create_custom_worker_role_seeds_empty_backing_fork(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """A new custom role gets an empty-prompt backing fork bound up front."""
    _seed_live_host(db_uri, "empty-fork-host")
    resp = await client.post(
        "/v1/agent-tasks/roles/worker",
        json={"slug": "scribe"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "worker:scribe"
    assert body["agent_name"].startswith("default-worker-fork-")
    # empty prompt by default
    assert body["prompt"] == "" or body["prompt"] is None


async def test_import_role_agent_rejects_non_packaged_source(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Import rejects an agent that isn't a packaged role agent for the kind."""
    _seed_live_host(db_uri, "import-reject-host")
    # task-manager is packaged for the manager kind, not worker
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore

    store = SqlAlchemyAgentStore(db_uri)
    manager = store.get_by_name("task-manager")
    assert manager is not None
    resp = await client.post(
        "/v1/agent-tasks/roles/worker:default/import-agent",
        json={"agent_id": manager.id},
    )
    assert resp.status_code == 400


async def test_create_and_delete_custom_manager_role(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    _seed_live_host(db_uri, "manager-role-host")
    create_resp = await client.post(
        "/v1/agent-tasks/roles/manager",
        json={"slug": "research", "agent_profile_id": task_manager_agent_id},
    )
    assert create_resp.status_code == 200
    body = create_resp.json()
    assert body["role"] == "manager:research"
    assert body["deletable"] is True
    assert body["system"] is False

    delete_resp = await client.delete(
        f"/v1/agent-tasks/roles/{quote('manager:research', safe='')}",
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True


async def test_patch_manager_role_key_pending_only(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    _seed_live_host(db_uri, "patch-manager-host")
    await client.post(
        "/v1/agent-tasks/roles/manager",
        json={"slug": "alt", "agent_profile_id": task_manager_agent_id},
    )
    created = (await client.post("/v1/agent-tasks", json=_create_payload())).json()
    pending_state = await client.patch(
        f"/v1/agent-tasks/{created['id']}",
        json={"state": "pending"},
    )
    assert pending_state.status_code == 200

    pending_patch = await client.patch(
        f"/v1/agent-tasks/{created['id']}",
        json={"manager_role_key": "manager:alt"},
    )
    assert pending_patch.status_code == 200
    assert pending_patch.json()["manager_role_key"] == "manager:alt"

    active_patch = await client.patch(
        f"/v1/agent-tasks/{created['id']}",
        json={"state": "active"},
    )
    assert active_patch.status_code == 200

    blocked_patch = await client.patch(
        f"/v1/agent-tasks/{created['id']}",
        json={"manager_role_key": "manager:default"},
    )
    assert blocked_patch.status_code == 409


async def _worker_lane_id(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    role_key: str = "worker:default",
) -> str:
    """Create and return a worker lane id."""
    worker_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/workers",
        json={"lanes": [{"role_key": role_key}]},
    )
    assert worker_resp.status_code == 200, worker_resp.text
    return worker_resp.json()["lanes"][role_key][0]


async def test_patch_worker_lane_role(
    client: httpx.AsyncClient,
    db_uri: str,
    task_manager_agent_id: str,
) -> None:
    """A lane that has not run yet can be re-pointed at another worker role."""
    _seed_live_host(db_uri, "worker-lane-host")
    await client.post(
        "/v1/agent-tasks/roles/worker",
        json={"slug": "reviewer", "agent_profile_id": task_manager_agent_id},
    )
    task_id = (await client.post("/v1/agent-tasks", json=_create_payload())).json()["id"]
    SqlAlchemyTaskStore(db_uri).update(task_id, state="idle")
    worker_id = await _worker_lane_id(client, task_id=task_id)

    patch_resp = await client.patch(
        task_worker_url(worker_id),
        json={"role_key": "worker:reviewer"},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["object"] == "agent.task.worker"
    assert body["role_key"] == "worker:reviewer"
    assert body["kind"] == "managed"
    assert body["agent_profile_id"] is None


async def test_patch_worker_lane_rejects_unknown_worker(client: httpx.AsyncClient) -> None:
    """An unknown lane id is a 404."""
    resp = await client.patch(
        task_worker_url(_uid("missing_worker")),
        json={"role_key": "worker:default"},
    )
    assert resp.status_code == 404


async def test_patch_worker_lane_rejects_non_worker_role(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Only worker roles may run a worker lane."""
    task_id = (await client.post("/v1/agent-tasks", json=_create_payload())).json()["id"]
    SqlAlchemyTaskStore(db_uri).update(task_id, state="idle")
    worker_id = await _worker_lane_id(client, task_id=task_id)

    resp = await client.patch(
        task_worker_url(worker_id),
        json={"role_key": "manager:default"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_input"


async def test_patch_worker_lane_conflicts_once_it_has_a_session(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """A lane that already ran keeps its history under the old role."""
    task_id = (await client.post("/v1/agent-tasks", json=_create_payload())).json()["id"]
    SqlAlchemyTaskStore(db_uri).update(task_id, state="idle")
    worker_id = await _worker_lane_id(client, task_id=task_id)
    SqlAlchemyWorkerStore(db_uri).update_worker(worker_id, session_id=_uid("lane_session"))

    resp = await client.patch(
        task_worker_url(worker_id),
        json={"role_key": "worker:default"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_secretary_profile_and_bootstrap(
    client: httpx.AsyncClient,
    task_manager_agent_id: str,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manager glossary defaults feed manager bootstrap."""
    _patch_workspace_validation(monkeypatch)
    from omnigent.agent_tasks.role_keys import MANAGER_DEFAULT_ROLE_KEY

    manager_host_id = _uid("manager_host")
    HostStore(db_uri).upsert_on_connect(manager_host_id, "manager-host", RESERVED_USER_LOCAL)
    profile_resp = await put_agent_role_profile(
        client,
        role=MANAGER_DEFAULT_ROLE_KEY,
        agent_profile_id=task_manager_agent_id,
        host_id=manager_host_id,
        workspace="/tmp/manager",
    )
    assert profile_resp.status_code == 200

    created = await client.post(
        "/v1/agent-tasks",
        json={"title": "Bootstrap me", "goal": "ship the feature"},
    )
    task_id = created.json()["id"]
    bootstrap_resp = await client.post(f"/v1/agent-tasks/{task_id}/bootstrap", json={})
    assert bootstrap_resp.status_code == 200
    assert bootstrap_resp.json()["manager_conversation_id"] is not None


async def _put_secretary_profile(
    client: httpx.AsyncClient,
    secretary_agent_id: str,
    *,
    db_uri: str,
) -> str:
    """PUT the secretary profile and register the host so workspace validation passes."""
    host_id = _uid("secretary_host")
    HostStore(db_uri).upsert_on_connect(host_id, "secretary-host", RESERVED_USER_LOCAL)
    profile_resp = await put_agent_role_profile(
        client,
        role=TASK_SECRETARY_ROLE,
        agent_profile_id=secretary_agent_id,
        host_id=host_id,
        workspace="/tmp/secretary",
    )
    assert profile_resp.status_code == 200
    body = profile_resp.json()
    assert body["agent_profile_id"] == secretary_agent_id
    assert "agent_id" not in body
    return host_id


async def test_ensure_secretary_session_starts_without_synthetic_items(
    client: httpx.AsyncClient,
    secretary_agent_id: str,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_validation(monkeypatch)
    await _put_secretary_profile(client, secretary_agent_id, db_uri=db_uri)

    ensure_resp = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_resp.status_code == 200
    body = ensure_resp.json()
    assert body["created"] is True
    conversation_id = body["conversation_id"]

    items_resp = await client.get(f"/v1/sessions/{conversation_id}/items")
    assert items_resp.status_code == 200
    assert items_resp.json()["data"] == []

    profile_resp = await client.get(agent_role_profile_url(TASK_SECRETARY_ROLE))
    assert profile_resp.json()["conversation_id"] == conversation_id

    ensure_again = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_again.status_code == 200
    assert ensure_again.json()["created"] is False
    assert ensure_again.json()["conversation_id"] == conversation_id


async def test_reset_secretary_session_starts_without_synthetic_items(
    client: httpx.AsyncClient,
    secretary_agent_id: str,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_validation(monkeypatch)
    await _put_secretary_profile(client, secretary_agent_id, db_uri=db_uri)
    first = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    first_id = first.json()["conversation_id"]

    reset_resp = await client.post(agent_role_session_reset_url(TASK_SECRETARY_ROLE))
    assert reset_resp.status_code == 200
    reset_body = reset_resp.json()
    assert reset_body["created"] is True
    assert reset_body["conversation_id"] != first_id

    deleted = await client.get(f"/v1/sessions/{first_id}")
    assert deleted.status_code == 404

    items_resp = await client.get(f"/v1/sessions/{reset_body['conversation_id']}/items")
    assert items_resp.json()["data"] == []

    profile_resp = await client.get(agent_role_profile_url(TASK_SECRETARY_ROLE))
    profile = profile_resp.json()
    assert profile["conversation_id"] == reset_body["conversation_id"]
    # Only the session is reset; the role keeps the harness and model it was given.
    assert profile["harness"] == "cursor"
    assert profile["model"] == "composer-2.5"


async def test_ensure_secretary_session_auto_provisions_profile(
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First ensure creates the profile and session without a prior PUT."""
    _patch_workspace_validation(monkeypatch)
    host_id = _uid("auto_secretary_host")
    HostStore(db_uri).upsert_on_connect(host_id, "auto-secretary-host", RESERVED_USER_LOCAL)

    ensure_resp = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_resp.status_code == 200
    body = ensure_resp.json()
    assert body["created"] is True

    profile_resp = await client.get(agent_role_profile_url(TASK_SECRETARY_ROLE))
    assert profile_resp.status_code == 200
    profile = profile_resp.json()
    assert profile["host_id"] == host_id
    assert profile["conversation_id"] == body["conversation_id"]


async def test_ensure_secretary_session_fails_when_no_host_available(
    client: httpx.AsyncClient,
) -> None:
    """Auto-provision refuses to create a profile when no live host exists."""
    ensure_resp = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_resp.status_code == 400
    assert ensure_resp.json()["error"]["message"] == NO_HOST_AVAILABLE_MESSAGE


async def test_create_and_delete_task_asset(client: httpx.AsyncClient) -> None:
    """Assets attach to a task and the DELETE route removes them."""
    task_id = (await client.post("/v1/agent-tasks", json=_create_payload())).json()["id"]

    create_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/assets",
        json={"kind": "url", "title": "PR #123", "url": "https://example.com/pr/123"},
    )
    assert create_resp.status_code == 200
    asset = create_resp.json()
    assert asset["object"] == "agent.task.asset"
    assert asset["title"] == "PR #123"
    asset_id = asset["id"]

    dashboard = (await client.get(f"/v1/agent-tasks/{task_id}/dashboard")).json()
    assert [a["id"] for a in dashboard["assets"]] == [asset_id]

    delete_resp = await client.delete(f"/v1/agent-tasks/{task_id}/assets/{asset_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    dashboard = (await client.get(f"/v1/agent-tasks/{task_id}/dashboard")).json()
    assert dashboard["assets"] == []

    # Deleting a missing asset is a 404.
    missing = await client.delete(f"/v1/agent-tasks/{task_id}/assets/{asset_id}")
    assert missing.status_code == 404
