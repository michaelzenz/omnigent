"""URL helpers for managed task agent role routes."""

from __future__ import annotations

import httpx


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
