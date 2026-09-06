"""Bootstrap a live manager session for a managed task."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.constants import (
    DEFAULT_TASK_HARNESS,
    DEFAULT_TASK_WORKSPACE,
    resolve_task_harness,
)
from omnigent.entities import Manager, Task
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.manager_store import ManagerStore
from omnigent.stores.task_store import TaskStore

_logger = logging.getLogger(__name__)

# Bootstraps for one owner serialize here so two concurrent attach-or-create
# runs can't both see an empty roster and spawn duplicate manager sessions.
# Process-local: fine for the single-server deployment; multi-replica would
# need a database-level lock.
_OWNER_BOOTSTRAP_LOCKS: dict[str, asyncio.Lock] = {}


def _owner_bootstrap_lock(owner: str) -> asyncio.Lock:
    return _OWNER_BOOTSTRAP_LOCKS.setdefault(owner, asyncio.Lock())


@dataclass(frozen=True)
class BootstrapParams:
    """Everything a role resolves to for one session spawn."""

    host_id: str
    workspace: str
    harness: str
    model: str | None
    agent_profile_id: str
    prompt_profile_id: str | None = None


def resolve_bootstrap_params(
    *,
    host_id: str | None,
    workspace: str | None,
    harness: str | None,
    model: str | None,
    role_profile: TaskRoleProfile | None,
) -> BootstrapParams:
    """Merge explicit bootstrap inputs over the role's defaults."""
    resolved_host_id = host_id or (role_profile.host_id if role_profile else None)
    resolved_workspace = os.path.expanduser(
        workspace or (role_profile.workspace if role_profile else None) or DEFAULT_TASK_WORKSPACE
    )
    resolved_harness = resolve_task_harness(
        harness or (role_profile.harness if role_profile else None) or DEFAULT_TASK_HARNESS
    )
    resolved_model = (
        model if model is not None else (role_profile.model if role_profile else None)
    ) or None
    resolved_agent_id = role_profile.agent_profile_id if role_profile else None
    if not resolved_host_id or not resolved_workspace:
        raise OmnigentError(
            "host_id and workspace are required to bootstrap a manager session",
            code=ErrorCode.INVALID_INPUT,
        )
    if not resolved_agent_id:
        raise OmnigentError(
            "the role must name an agent profile to bootstrap a session",
            code=ErrorCode.INVALID_INPUT,
        )
    return BootstrapParams(
        host_id=resolved_host_id,
        workspace=resolved_workspace,
        harness=resolved_harness,
        model=resolved_model,
        agent_profile_id=resolved_agent_id,
        prompt_profile_id=role_profile.prompt_profile_id if role_profile else None,
    )


_PUPPYGARDEN_PROJECT_NAME = "PuppyGarden"


def ensure_puppygarden_project(
    project_store: Any,
    user_id: str | None,
) -> str | None:
    """Find or create the owner's "PuppyGarden" project, return its id.

    Called by role session bootstraps so broker, secretary, and manager
    sessions are filed into one project instead of cluttering the flat
    sessions list. Returns None when no project store is wired.
    """
    if project_store is None:
        return None
    for proj in project_store.list(user_id=user_id):
        if proj.name == _PUPPYGARDEN_PROJECT_NAME:
            return proj.id
    try:
        proj = project_store.create(
            uuid.uuid4().hex,
            _PUPPYGARDEN_PROJECT_NAME,
            user_id,
        )
        return proj.id
    except OmnigentError as exc:
        if exc.code == ErrorCode.ALREADY_EXISTS:
            for proj in project_store.list(user_id=user_id):
                if proj.name == _PUPPYGARDEN_PROJECT_NAME:
                    return proj.id
        raise


def build_role_session_request(
    profile: TaskRoleProfile,
    *,
    title: str,
    labels: dict[str, str] | None = None,
    parent_session_id: str | None = None,
    sub_agent_name: str | None = None,
    overrides: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> Any:
    """Build a ``SessionCreateRequest`` from a glossary role profile.

    This lets role bootstraps (secretary, broker, manager, worker) go
    through the same ``create_session_internal`` path as user-initiated
    ``POST /v1/sessions`` — validation, runner launch, permissions,
    adoption, and terminal-first flags all come for free.

    :param profile: The glossary role profile (agent, host, workspace,
        harness, model).
    :param title: Session title.
    :param labels: Role labels (e.g. ``{ROLE_LABEL: SECRETARY_ROLE_VALUE}``).
    :param parent_session_id: Parent conversation id for worker
        sub-agent sessions. ``None`` for top-level roles.
    :param sub_agent_name: Sub-agent type name within the parent's spec
        tree. ``None`` for top-level roles and for workers that bind
        their own ``agent_id`` directly.
    :param overrides: Optional dict merged into the request body to
        override profile-derived values (e.g. ``terminal_launch_args``).
    :returns: A ``SessionCreateRequest`` instance.
    """
    from omnigent.server.schemas import SessionCreateRequest

    params = resolve_bootstrap_params(
        host_id=profile.host_id,
        workspace=profile.workspace,
        harness=profile.harness,
        model=profile.model,
        role_profile=profile,
    )
    body = SessionCreateRequest(
        agent_id=params.agent_profile_id,
        title=title,
        host_id=params.host_id,
        workspace=params.workspace,
        harness_override=params.harness,
        model_override=params.model,
        labels=labels or {},
        parent_session_id=parent_session_id,
        sub_agent_name=sub_agent_name,
        prompt_profile=(
            {"mode": "fixed", "profile_id": profile.prompt_profile_id}
            if profile.prompt_profile_id
            else None
        ),
        project_id=project_id,
    )
    if overrides:
        for key, value in overrides.items():
            setattr(body, key, value)
    return body


async def bootstrap_task_manager(
    *,
    task: Task,
    task_store: TaskStore,
    conversation_store: ConversationStore,
    session_creator: Any,
    app_state: Any,
    user_id: str | None = None,
) -> Task:
    """
    Ensure ``task`` is bound to a live manager.

    Attach-or-create: the task first joins the best host-compatible manager
    with capacity (one manager owns a portfolio of tasks); a new manager is
    created only when none fits.

    Idempotent when ``manager_id`` points at a manager whose session exists.
    A manager row whose session pointer is dead is healed in place — a fresh
    session is created for the same durable id — so the manager's identity,
    tasks, and queue all survive. A task with no manager falls through to
    attach-or-create.

    The session is created through ``create_session_internal`` (the same path
    as ``POST /v1/sessions``) so workspace validation, runner launch,
    permissions, and adoption all apply.

    Concurrent bootstraps for the same owner serialize on a per-owner lock,
    so a cold-start burst (two rapid creates, two package accepts) can never
    spawn duplicate managers — the loser re-reads the roster and attaches to
    the winner's manager.
    """
    manager_store: ManagerStore | None = getattr(app_state, "manager_store", None)
    if manager_store is None:
        from omnigent.stores.manager_store.sqlalchemy_store import (
            SqlAlchemyManagerStore,
        )

        manager_store = SqlAlchemyManagerStore(task_store.storage_location)
    if task.manager_id is None:
        raise OmnigentError(
            f"task {task.id} has no manager; create one via "
            "POST /agent-tasks/managers and attach it",
            code=ErrorCode.CONFLICT,
        )
    # The liveness check + heal runs under the per-owner lock: concurrent
    # bootstraps for one owner must not double-heal a dead session pointer.
    # The heal re-reads the row inside the lock so the loser of a race
    # observes the winner's fresh pointer.
    owner_user_id = user_id or task.owner_user_id or "__anonymous__"
    async with _owner_bootstrap_lock(owner_user_id):
        manager = await asyncio.to_thread(manager_store.get, task.manager_id)
        if manager is None:
            raise OmnigentError(
                f"task {task.id} references manager {task.manager_id} which does not exist",
                code=ErrorCode.NOT_FOUND,
            )
        await ensure_manager_session(
            manager,
            manager_store=manager_store,
            conversation_store=conversation_store,
            session_creator=session_creator,
            app_state=app_state,
        )
        return task


async def _session_request_for_manager(
    manager: Manager,
    *,
    app_state: Any,
) -> Any:
    """Build a ``SessionCreateRequest`` purely from the manager row.

    The row's execution snapshot (title, host, workspace, harness, model,
    agent/prompt profiles) is the single source of truth for re-creation —
    immune to role-profile edits and independent of any task.
    """
    from omnigent.agent_tasks.session_labels import presentation_labels_for_harness
    from omnigent.server.schemas import SessionCreateRequest

    if not manager.agent_profile_id:
        raise OmnigentError(
            f"manager {manager.id} has no agent profile snapshot; cannot re-create its session",
            code=ErrorCode.INTERNAL_ERROR,
        )
    return SessionCreateRequest(
        agent_id=manager.agent_profile_id,
        title=manager.title or "Task manager",
        host_id=manager.host_id,
        workspace=manager.workspace,
        harness_override=manager.harness,
        model_override=manager.model,
        labels=presentation_labels_for_harness(manager.harness),
        prompt_profile=(
            {"mode": "fixed", "profile_id": manager.prompt_profile_id}
            if manager.prompt_profile_id
            else None
        ),
        project_id=await asyncio.to_thread(
            ensure_puppygarden_project,
            getattr(app_state, "project_store", None),
            None if manager.owner_user_id == "__anonymous__" else manager.owner_user_id,
        ),
    )


async def ensure_manager_session(
    manager: Manager,
    *,
    manager_store: ManagerStore,
    conversation_store: ConversationStore,
    session_creator: Any,
    app_state: Any,
) -> Manager:
    """Ensure the manager's session is live, healing the pointer if not.

    The single heal implementation: a dead or missing session is re-created
    purely from the manager row's stored snapshot — same durable id, same
    execution params. Callers holding a stale ``manager`` must re-read the
    returned row for the fresh pointer.

    Caller is responsible for serialization (the bootstrap owner lock, or
    the dispatcher's single-flight delivery).
    """
    from omnigent.server.routes.sessions import _make_internal_request

    if manager_store is None or session_creator is None or app_state is None:
        raise OmnigentError(
            "manager persistence is not configured on this server",
            code=ErrorCode.INTERNAL_ERROR,
        )
    session_id = manager.conversation_id
    if session_id is not None and await asyncio.to_thread(
        conversation_store.get_conversation, session_id
    ):
        return manager

    _logger.info(
        "manager heal: session for manager %s is gone; re-creating from stored snapshot",
        manager.id,
    )
    body = await _session_request_for_manager(manager, app_state=app_state)
    resp = await session_creator(
        body=body,
        request=_make_internal_request(app_state),
        user_id=None if manager.owner_user_id == "__anonymous__" else manager.owner_user_id,
    )
    updated = await asyncio.to_thread(
        manager_store.update,
        manager.id,
        conversation_id=resp.id,
    )
    if updated is None:
        raise OmnigentError(
            "manager row disappeared during heal",
            code=ErrorCode.INTERNAL_ERROR,
        )
    return updated


async def spawn_manager_session(
    *,
    params: BootstrapParams,
    owner_user_id: str,
    role_key: str,
    title: str,
    description: str,
    manager_store: ManagerStore,
    session_creator: Any,
    app_state: Any,
    user_id: str | None = None,
) -> Manager:
    """Create one manager session and register its durable, self-describing row.

    The execution snapshot (host, workspace, harness, model, agent/prompt
    profiles) is stored on the row at spawn so every later heal re-creates
    the session from the row alone.

    :returns: the durable manager row bound to the new session.
    """
    from omnigent.server.routes.sessions import _make_internal_request
    from omnigent.server.schemas import SessionCreateRequest

    if session_creator is None or app_state is None:
        raise OmnigentError(
            "manager persistence is not configured on this server",
            code=ErrorCode.INTERNAL_ERROR,
        )

    body = SessionCreateRequest(
        agent_id=params.agent_profile_id,
        title=title,
        host_id=params.host_id,
        workspace=params.workspace,
        harness_override=params.harness,
        model_override=params.model,
        prompt_profile=(
            {"mode": "fixed", "profile_id": params.prompt_profile_id}
            if params.prompt_profile_id
            else None
        ),
        project_id=await asyncio.to_thread(
            ensure_puppygarden_project,
            getattr(app_state, "project_store", None),
            user_id,
        ),
    )
    resp = await session_creator(
        body=body,
        request=_make_internal_request(app_state),
        user_id=user_id,
    )
    return await asyncio.to_thread(
        manager_store.upsert,
        uuid.uuid4().hex,
        owner_user_id=owner_user_id,
        role_key=role_key,
        title=title,
        description=description,
        conversation_id=resp.id,
        host_id=params.host_id,
        workspace=params.workspace,
        harness=params.harness,
        model=params.model,
        agent_profile_id=params.agent_profile_id,
        prompt_profile_id=params.prompt_profile_id,
    )
