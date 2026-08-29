"""Bootstrap a live manager session for a managed task."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

from omnigent.agent_tasks.constants import (
    DEFAULT_TASK_HARNESS,
    DEFAULT_TASK_WORKSPACE,
    resolve_task_harness,
)
from omnigent.agent_tasks.manager_discovery import (
    choose_manager_for_task,
    list_active_managers,
)
from omnigent.entities import Task
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
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


def build_role_session_request(
    profile: TaskRoleProfile,
    *,
    title: str,
    labels: dict[str, str] | None = None,
    parent_session_id: str | None = None,
    sub_agent_name: str | None = None,
    overrides: dict[str, Any] | None = None,
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
    params: BootstrapParams,
    session_creator: Any,
    app_state: Any,
    user_id: str | None = None,
) -> Task:
    """
    Ensure ``task`` has a live manager conversation.

    Attach-or-create: the task first joins the best host-compatible manager
    with capacity (one manager owns a portfolio of tasks); a new manager
    session is spawned only when no existing manager fits.

    Idempotent when ``manager_conversation_id`` points at an existing
    conversation; a missing session falls through to re-run attach-or-create.

    The session is created through ``create_session_internal`` (the same path
    as ``POST /v1/sessions``) so workspace validation, runner launch,
    permissions, and adoption all apply.

    Concurrent bootstraps for the same owner serialize on a per-owner lock,
    so a cold-start burst (two rapid creates, two package accepts) can never
    spawn duplicate managers — the loser re-reads the roster and attaches to
    the winner's session.
    """
    if task.manager_conversation_id is not None:
        existing = await asyncio.to_thread(
            conversation_store.get_conversation,
            task.manager_conversation_id,
        )
        if existing is not None:
            return task
        # The stored manager session is gone — fall through to re-run
        # attach-or-create rather than stranding the task in CONFLICT. With
        # N tasks sharing one manager, a dead session must not block the
        # whole portfolio.
        _logger.info(
            "manager bootstrap: stored manager %s for task %s is gone; re-attaching",
            task.manager_conversation_id,
            task.id,
        )

    # Attach-or-create runs under the per-owner lock: the first cold-start
    # bootstrap spawns, later ones re-read the roster and attach to it.
    owner = user_id or task.owner_user_id or "__anonymous__"
    async with _owner_bootstrap_lock(owner):
        return await _attach_or_create_manager(
            task=task,
            task_store=task_store,
            conversation_store=conversation_store,
            params=params,
            session_creator=session_creator,
            app_state=app_state,
            user_id=user_id,
            owner=owner,
        )


async def _attach_or_create_manager(
    *,
    task: Task,
    task_store: TaskStore,
    conversation_store: ConversationStore,
    params: BootstrapParams,
    session_creator: Any,
    app_state: Any,
    user_id: str | None,
    owner: str,
) -> Task:
    """The unlocked attach-or-create body — caller holds the owner lock."""
    managers = await asyncio.to_thread(
        list_active_managers,
        owner_user_id=owner,
        task_store=task_store,
        conversation_store=conversation_store,
    )
    chosen = choose_manager_for_task(managers, probe=task, host_id=params.host_id)
    if chosen is not None:
        _logger.info(
            "manager attach: task %s -> manager %s (candidates=%d, host=%s)",
            task.id,
            chosen.conversation_id,
            len(managers),
            params.host_id,
        )
        updated = await asyncio.to_thread(
            task_store.update,
            task.id,
            manager_conversation_id=chosen.conversation_id,
        )
        if updated is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        return updated

    from omnigent.agent_tasks.session_labels import presentation_labels_for_harness
    from omnigent.server.routes.sessions import _make_internal_request
    from omnigent.server.schemas import SessionCreateRequest

    _logger.info(
        "manager spawn: task %s gets a new manager session (candidates=%d, host=%s)",
        task.id,
        len(managers),
        params.host_id,
    )
    body = SessionCreateRequest(
        agent_id=params.agent_profile_id,
        title=f"Task manager: {task.title}",
        host_id=params.host_id,
        workspace=params.workspace,
        harness_override=params.harness,
        model_override=params.model,
        # Carry the native wrapper label when the role is on a native harness so
        # the composer's model picker opts the session in. Empty for the SDK
        # harness — the dock surfaces its own switcher there.
        labels=presentation_labels_for_harness(params.harness),
        prompt_profile=(
            {"mode": "fixed", "profile_id": params.prompt_profile_id}
            if params.prompt_profile_id
            else None
        ),
    )
    request = _make_internal_request(app_state)
    resp = await session_creator(
        body=body,
        request=request,
        user_id=user_id,
    )
    updated = await asyncio.to_thread(
        task_store.update,
        task.id,
        manager_conversation_id=resp.id,
    )
    if updated is None:
        raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
    return updated
