"""Worker slot helpers for managed task items."""

from __future__ import annotations

import uuid

from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.entities import Task, TaskItem, Worker
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore


def _generate_worker_id() -> str:
    return uuid.uuid4().hex


def assign_worker_profile(
    *,
    item: TaskItem,
    role_key: str,
    worker_store: WorkerStore,
    task_item_store: TaskItemStore,
) -> tuple[TaskItem, Worker]:
    """Bind an item's worker lane to a role, creating the lane when needed."""
    stripped = role_key.strip()
    if not stripped:
        raise OmnigentError("role_key must be non-empty", code=ErrorCode.INVALID_INPUT)
    if item.worker_id is not None:
        existing = worker_store.get_worker(item.worker_id)
        if existing is not None:
            if existing.role_key == stripped:
                return item, existing
            # A lane that has not run yet can be re-pointed at another role;
            # once it holds a session the history belongs to the old role.
            if existing.session_id is None:
                rebound = worker_store.update_worker(existing.id, role_key=stripped)
                if rebound is None:
                    raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
                return item, rebound
    worker = worker_store.create_worker(
        _generate_worker_id(),
        item.task_id,
        role_key=stripped,
    )
    updated = task_item_store.update_item(item.id, worker_id=worker.id)
    if updated is None:
        raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
    return updated, worker


def worker_for_item(
    item: TaskItem,
    *,
    worker_store: WorkerStore,
) -> Worker | None:
    """Return the worker slot assigned to an item, if any."""
    if item.worker_id is None:
        return None
    return worker_store.get_worker(item.worker_id)


def activate_worker_lane(
    *,
    task: Task,
    worker: Worker,
    task_store: TaskStore,
    worker_store: WorkerStore,
    conversation_store: ConversationStore,
    manager_role_profile: TaskRoleProfile | None,
    worker_role_profile: TaskRoleProfile | None,
) -> tuple[Worker, str]:
    """Start a worker sub-agent session for a lane that has not run yet."""
    if worker.task_id != task.id:
        raise OmnigentError("Worker does not belong to task", code=ErrorCode.INVALID_INPUT)
    if worker.kind == "external":
        raise OmnigentError(
            "External worker lanes cannot be activated",
            code=ErrorCode.CONFLICT,
        )
    if worker.session_id is not None:
        existing = conversation_store.get_conversation(worker.session_id)
        if existing is not None:
            return worker, worker.session_id
        raise OmnigentError(
            "Worker session is missing; clear session_id before re-activate",
            code=ErrorCode.CONFLICT,
        )
    role_key = (worker.role_key or "").strip()
    if not role_key:
        raise OmnigentError(
            "worker role must be set before activate",
            code=ErrorCode.INVALID_INPUT,
        )
    if worker_role_profile is None:
        raise OmnigentError(
            f"Task role profile not found: {role_key}",
            code=ErrorCode.NOT_FOUND,
        )

    from omnigent.agent_tasks.items import ensure_task_manager_for_dispatch

    task = ensure_task_manager_for_dispatch(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        role_profile=manager_role_profile,
        host_id=worker_role_profile.host_id,
        workspace=worker_role_profile.workspace,
        harness=worker_role_profile.harness,
        model=worker_role_profile.model,
    )
    manager_conversation_id = task.manager_conversation_id
    if manager_conversation_id is None:
        raise OmnigentError(
            "Task manager is not bootstrapped",
            code=ErrorCode.CONFLICT,
        )
    manager_conv = conversation_store.get_conversation(manager_conversation_id)
    if manager_conv is None:
        raise OmnigentError(
            "Manager session is missing",
            code=ErrorCode.CONFLICT,
        )
    if manager_conv.agent_id is None:
        raise OmnigentError(
            "Manager session has no agent binding",
            code=ErrorCode.CONFLICT,
        )

    bootstrap = resolve_bootstrap_params(
        host_id=worker_role_profile.host_id,
        workspace=worker_role_profile.workspace,
        harness=worker_role_profile.harness,
        model=worker_role_profile.model,
        role_profile=worker_role_profile,
    )
    worker_conv = conversation_store.create_conversation(
        kind="sub_agent",
        title=role_key,
        parent_conversation_id=manager_conversation_id,
        agent_id=bootstrap.agent_profile_id,
        runner_id=manager_conv.runner_id,
        host_id=bootstrap.host_id,
        workspace=bootstrap.workspace,
    )
    conversation_store.update_conversation(
        worker_conv.id,
        harness_override=bootstrap.harness,
        model_override=bootstrap.model,
        _unset_model_override=bootstrap.model is None,
    )
    updated = worker_store.update_worker(
        worker.id,
        session_id=worker_conv.id,
    )
    if updated is None:
        raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
    return updated, worker_conv.id
