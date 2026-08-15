"""Internal session-creation entry point for background callers.

``create_session_internal`` wraps the full ``POST /v1/sessions`` JSON path
(validation, runner launch, permissions, adoption, terminal-first flags,
managed-host launch) so managed-task role bootstraps (secretary, broker,
manager, worker) reuse the same mechanism as user-initiated creates.
Background callers lack a real FastAPI ``Request`` and pass the minimal
``Request``-like object built by ``_make_internal_request``.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any, Callable

import httpx
from fastapi import Request

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runtime.agent_cache import AgentCache
from omnigent.runner.routing import RunnerRouter
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.routes._auth_helpers import get_permission_level as _get_permission_level
from omnigent.server.routes._sessions.common import (
    _CLAUDE_NATIVE_UI_LABEL_KEY,
    _CLAUDE_NATIVE_UI_LABEL_VALUE,
    _managed_launch_tasks,
)
from omnigent.server.routes._sessions.helpers import (
    _announce_session_added,
    _get_runner_client,
    _publish_sandbox_status,
    _publish_terminal_pending,
)
from omnigent.server.routes._sessions.orchestration import (
    _create_session_from_existing_agent,
    _run_managed_launch,
)
from omnigent.server.schemas import SessionCreateRequest, SessionResponse
from omnigent.stores import AgentStore, ConversationStore
from omnigent.stores.artifact_store import ArtifactStore
from omnigent.stores.file_store import FileStore
from omnigent.stores.permission_store import PermissionStore

_logger = logging.getLogger("omnigent.server.routes.sessions")


def _make_internal_request(app_state: Any) -> Any:
    """Build a minimal Request-like object for background session creation.

    ``create_session_internal`` and ``_create_session_from_existing_agent``
    read ``request.app.state`` (host registry, host store, tunnel registry)
    and ``request.headers`` (telemetry). Background callers that lack a
    real FastAPI ``Request`` pass this mock so they reuse the same create
    path as user-initiated ``POST /v1/sessions``.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        app=SimpleNamespace(state=app_state),
        headers={},
    )


async def create_session_internal(
    *,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    runner_router: RunnerRouter | None,
    agent_cache: AgentCache | None,
    permission_store: PermissionStore | None,
    liveness_lookup: Callable[..., Any] | None,
    file_store: FileStore | None,
    artifact_store: ArtifactStore | None,
    body: SessionCreateRequest,
    request: Request,
    user_id: str | None,
) -> SessionResponse:
    """Create a session from a validated request, reusing the full
    ``POST /v1/sessions`` JSON path — validation, runner launch, permissions,
    adoption, terminal-first flags.

    Called by the ``create_session`` route handler for user-initiated
    creates, and by managed-task role bootstraps (secretary, broker,
    manager, worker) so every session goes through the same mechanism.
    Background callers pass a minimal ``Request``-like object built by
    ``_make_internal_request`` whose ``app.state`` carries the server's
    host registry and stores.
    """
    resp = await _create_session_from_existing_agent(
        conversation_store,
        agent_store,
        runner_router,
        body,
        request,
        agent_cache=agent_cache,
        user_id=user_id,
        permission_store=permission_store,
        liveness_lookup=liveness_lookup,
        file_store=file_store,
        artifact_store=artifact_store,
    )
    conv = conversation_store.get_conversation(resp.id)
    _terminal_first_create = (
        conv is not None
        and body.host_id is not None
        and conv.labels.get(_CLAUDE_NATIVE_UI_LABEL_KEY) == _CLAUDE_NATIVE_UI_LABEL_VALUE
    )
    if _terminal_first_create:
        _publish_terminal_pending(resp.id, True)
    _rc = await _get_runner_client(resp.id, runner_router)
    if _rc is not None and conv is not None:
        try:
            await _rc.post(
                "/v1/sessions",
                json={
                    "session_id": resp.id,
                    "agent_id": conv.agent_id,
                    "sub_agent_name": conv.sub_agent_name,
                },
                timeout=10.0,
            )
        except (httpx.HTTPError, ConnectionError):
            _logger.warning(
                "Failed to notify runner about session %s",
                resp.id,
                exc_info=True,
            )
    if permission_store is not None and user_id is not None:
        await asyncio.to_thread(permission_store.ensure_user, user_id)
        await asyncio.to_thread(permission_store.grant, user_id, resp.id, LEVEL_OWNER)
        resp.permission_level = await _get_permission_level(user_id, resp.id, permission_store)
    _announce_session_added(user_id, resp.id)
    from omnigent.agent_tasks.adoption import notify_new_session

    await notify_new_session(resp.id, user_id=user_id, host_id=body.host_id)

    launch_host_id = body.host_id
    if body.host_type == "managed" and resp.runner_id is None:
        sandbox_config = getattr(request.app.state, "sandbox_config", None)
        host_store_for_managed = getattr(request.app.state, "host_store", None)
        managed_launches = getattr(request.app.state, "managed_launches", None)
        if sandbox_config is None or host_store_for_managed is None or managed_launches is None:
            raise OmnigentError(
                "managed hosts are not configured on this server — add a "
                "'sandbox:' section to the server config",
                code=ErrorCode.INVALID_INPUT,
            )
        from omnigent.server.auth import RESERVED_USER_LOCAL
        from omnigent.server.managed_hosts import (
            MANAGED_REPO_LABEL_KEY,
            parse_repo_workspace,
        )

        repo = parse_repo_workspace(body.workspace) if body.workspace is not None else None
        if body.workspace is not None:
            await asyncio.to_thread(
                conversation_store.set_labels,
                resp.id,
                {MANAGED_REPO_LABEL_KEY: body.workspace},
            )
        managed_launches.begin(resp.id)
        _publish_sandbox_status(resp.id, "provisioning")
        launch_task = asyncio.create_task(
            _run_managed_launch(
                session_id=resp.id,
                owner=user_id if user_id is not None else RESERVED_USER_LOCAL,
                sandbox_config=sandbox_config,
                repo=repo,
                tracker=managed_launches,
                conversation_store=conversation_store,
                host_store=host_store_for_managed,
                host_registry=getattr(request.app.state, "host_registry", None),
                tunnel_registry=getattr(request.app.state, "tunnel_registry", None),
            )
        )
        _managed_launch_tasks.add(launch_task)
        launch_task.add_done_callback(_managed_launch_tasks.discard)

    if launch_host_id is not None and resp.runner_id is None:
        host_registry = getattr(request.app.state, "host_registry", None)
        host_store_inst = getattr(request.app.state, "host_store", None)
        if host_registry is not None and host_store_inst is not None:
            from omnigent.host.frames import (
                HostLaunchRunnerFrame,
                encode_host_frame,
            )
            from omnigent.runner.identity import token_bound_runner_id
            from omnigent.server.routes._host_launch import resolve_host_launch

            target = await asyncio.to_thread(
                resolve_host_launch,
                user_id=user_id,
                host_id=launch_host_id,
                session_id=resp.id,
                host_store=host_store_inst,
                host_registry=host_registry,
                conversation_store=conversation_store,
                permission_store=permission_store,
            )
            conn = target.conn
            binding_token = secrets.token_urlsafe(32)
            runner_id = token_bound_runner_id(binding_token)
            bound = await asyncio.to_thread(
                conversation_store.set_runner_id,
                resp.id,
                runner_id,
            )
            if not bound:
                raise OmnigentError(
                    f"Session {resp.id!r} already has a runner bound",
                    code=ErrorCode.CONFLICT,
                )
            request_id = secrets.token_hex(8)
            future: asyncio.Future[dict[str, str | None]] = (
                asyncio.get_running_loop().create_future()
            )
            conn.pending_launches[request_id] = future
            if resp.workspace is None:  # pragma: no cover — schema guards
                raise OmnigentError(
                    "session has host_id but no workspace; "
                    "schema constraint should have prevented this",
                    code=ErrorCode.INTERNAL_ERROR,
                )
            launch_frame = encode_host_frame(
                HostLaunchRunnerFrame(
                    request_id=request_id,
                    binding_token=binding_token,
                    workspace=resp.workspace,
                    session_id=resp.id,
                    harness=resp.harness,
                )
            )
            host_registry.send_text(conn, launch_frame)
            try:
                result = await asyncio.wait_for(future, timeout=30.0)
            except asyncio.TimeoutError:
                conn.pending_launches.pop(request_id, None)
                result = {"status": "failed", "error": "host launch timed out"}
            if result.get("status") == "failed":
                _logger.warning(
                    "Host %s failed to launch runner for session %s: %s",
                    launch_host_id,
                    resp.id,
                    result.get("error"),
                )
                if _terminal_first_create:
                    _publish_terminal_pending(resp.id, False)
            resp.runner_id = runner_id
            resp.host_id = launch_host_id

    return resp
