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
    model: str = "composer-2.5",
) -> httpx.Response:
    return await client.put(
        agent_role_profile_url(role),
        json={
            "agent_profile_id": agent_profile_id,
            "host_id": host_id,
            "workspace": workspace,
            "harness": harness,
            "model": model,
        },
    )


def patch_host_session_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub host validation and launch for in-process task route tests."""

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
