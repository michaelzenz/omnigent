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
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore
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
    assert created["state"] == "idle"
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
    idle_task = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(title="Idle task"),
    )
    archived = await client.post(
        "/v1/agent-tasks",
        json=_create_payload(title="Archived task"),
    )
    await client.delete(f"/v1/agent-tasks/{archived.json()['id']}")

    list_resp = await client.get("/v1/agent-tasks?state=idle")
    assert list_resp.status_code == 200
    ids = {row["id"] for row in list_resp.json()["data"]}
    assert idle_task.json()["id"] in ids
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
    assert (
        create_with_desc.json()["description"] == "Implements coding task items."
    )


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
    """Create an item bound to a worker lane and return the lane id."""
    item_resp = await client.post(
        f"/v1/agent-tasks/{task_id}/items",
        json={"title": "Investigate failure", "worker_role_key": role_key},
    )
    assert item_resp.status_code == 200
    worker_id = item_resp.json()["worker_id"]
    assert worker_id is not None
    return worker_id


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


async def test_patch_worker_lane_rejects_non_worker_role(client: httpx.AsyncClient) -> None:
    """Only worker roles may run a worker lane."""
    task_id = (await client.post("/v1/agent-tasks", json=_create_payload())).json()["id"]
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

    created = await client.post("/v1/agent-tasks", json={"title": "Bootstrap me"})
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


async def test_ensure_secretary_session_seeds_prompt(
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
    items = items_resp.json()["data"]
    assert len(items) == 1
    assert items[0]["role"] == "user"
    assert items[0].get("is_meta") is True
    assert "docs/agent-tasks/API_REFERENCE.md" in items_resp.text
    assert "docs/agent-tasks/TASK_SECRETARY.md" in items_resp.text
    assert "secretary" in items_resp.text.lower()

    profile_resp = await client.get(agent_role_profile_url(TASK_SECRETARY_ROLE))
    assert profile_resp.json()["conversation_id"] == conversation_id

    ensure_again = await client.post(agent_role_session_url(TASK_SECRETARY_ROLE))
    assert ensure_again.status_code == 200
    assert ensure_again.json()["created"] is False
    assert ensure_again.json()["conversation_id"] == conversation_id


async def test_reset_secretary_session_reseeds_prompt(
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
    items = items_resp.json()["data"]
    assert len(items) == 1
    assert items[0].get("is_meta") is True
    assert "docs/agent-tasks/API_REFERENCE.md" in items_resp.text
    assert "docs/agent-tasks/TASK_SECRETARY.md" in items_resp.text

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
