"""URL helpers for managed task agent role routes."""

from __future__ import annotations

import httpx
import pytest


def agent_role_profile_url(role: str) -> str:
    from urllib.parse import quote

    return f"/v1/agent-tasks/roles/{quote(role, safe='')}/profile"


def agent_role_session_url(role: str) -> str:
    return f"/v1/agent-tasks/roles/{role}/session"


def agent_role_session_reset_url(role: str) -> str:
    return f"/v1/agent-tasks/roles/{role}/session/reset"


def task_worker_url(worker_id: str) -> str:
    return f"/v1/task-workers/{worker_id}"


async def put_agent_role_profile(
    client: httpx.AsyncClient,
    *,
    role: str,
    agent_profile_id: str,
    host_id: str,
    workspace: str,
    harness: str = "cursor",
    model: str | None = None,
    description: str | None = None,
) -> httpx.Response:
    body: dict[str, object] = {
        "agent_profile_id": agent_profile_id,
        "host_id": host_id,
        "workspace": workspace,
        "harness": harness,
    }
    if model is not None:
        body["model"] = model
    if description is not None:
        body["description"] = description
    return await client.put(
        agent_role_profile_url(role),
        json=body,
    )


def patch_host_session_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub host validation and launch for in-process task route tests."""

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


def seed_owned_manager(db_uri: str, seed: str) -> str:
    """Register a first-class manager and return its durable row id.

    Tasks are born attached to a first-class manager, so route tests that
    create tasks need a manager row owned by the anonymous test caller.
    """
    import uuid as _uuid

    def _uid(seed: str) -> str:
        return _uuid.uuid5(_uuid.NAMESPACE_DNS, seed).hex

    from omnigent.db.utils import generate_agent_id
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
    from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore

    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name=f"mgr-{seed}", bundle_location="test:///bundle")

    host_id = _uid(f"{seed}-host")
    manager_conversation_id = _uid(f"{seed}-conv")
    SqlAlchemyConversationStore(db_uri).create_conversation(
        conversation_id=manager_conversation_id,
        title="Test manager",
        agent_id=agent_id,
        host_id=host_id,
        workspace="/tmp/test-manager",
    )
    manager_row_id = _uid(f"{seed}-row")
    SqlAlchemyManagerStore(db_uri).upsert(
        manager_row_id,
        conversation_id=manager_conversation_id,
        owner_user_id="__anonymous__",
        role_key="manager:default",
        description="Owns test work.",
        host_id=host_id,
        workspace="/tmp/test-manager",
        harness="cursor",
        model="composer-2.5",
        agent_profile_id=agent_id,
    )
    return manager_row_id
