"""Routes for managed agent tasks (``/v1/agent-tasks``).

Tasks are long-lived work units owned by a manager agent. This router
exposes CRUD for tasks and tags plus read-only execution history. Event
ingress and manager wake are handled in later phases.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from omnigent.db.enum_codecs import TASK_STATE
from omnigent.entities import Task, TaskEventExecution, TaskTag
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_user
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_VALID_TASK_STATES = frozenset(TASK_STATE)


def _generate_task_id() -> str:
    """Generate a unique task identifier.

    :returns: A bare 32-char hex uuid.
    """
    return uuid.uuid4().hex


class TaskTagInput(BaseModel):
    """One typed tag on a task."""

    tag_type: str
    tag: str

    @field_validator("tag_type", "tag")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be a non-empty string")
        return stripped


class CreateAgentTaskRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks``."""

    manager_agent_id: str
    title: str
    description: str | None = None
    charter: str | None = None
    manager_conversation_id: str | None = None
    state: str = "active"
    tags: list[TaskTagInput] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped

    @field_validator("state")
    @classmethod
    def _validate_state(cls, value: str) -> str:
        if value not in _VALID_TASK_STATES:
            allowed = ", ".join(sorted(_VALID_TASK_STATES))
            raise ValueError(f"state must be one of: {allowed}")
        return value


class UpdateAgentTaskRequest(BaseModel):
    """Request body for ``PATCH /v1/agent-tasks/{task_id}``."""

    title: str | None = None
    description: str | None = None
    charter: str | None = None
    manager_agent_id: str | None = None
    manager_conversation_id: str | None = None
    state: str | None = None

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped

    @field_validator("state")
    @classmethod
    def _validate_state(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in _VALID_TASK_STATES:
            allowed = ", ".join(sorted(_VALID_TASK_STATES))
            raise ValueError(f"state must be one of: {allowed}")
        return value


class PutTaskTagsRequest(BaseModel):
    """Request body for ``PUT /v1/agent-tasks/{task_id}/tags``."""

    tags: list[TaskTagInput] = Field(default_factory=list)


def _tag_to_response(tag: TaskTag) -> dict[str, str]:
    return {"tag_type": tag.tag_type, "tag": tag.tag}


def _task_to_response(task: Task, *, tags: list[TaskTag] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": task.id,
        "object": "agent.task",
        "manager_agent_id": task.manager_agent_id,
        "manager_conversation_id": task.manager_conversation_id,
        "owner_user_id": task.owner_user_id,
        "title": task.title,
        "description": task.description,
        "charter": task.charter,
        "search_text": task.search_text,
        "state": task.state,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }
    if tags is not None:
        result["tags"] = [_tag_to_response(tag) for tag in tags]
    return result


def _execution_to_response(execution: TaskEventExecution) -> dict[str, Any]:
    return {
        "id": execution.id,
        "object": "agent.task.execution",
        "event_id": execution.event_id,
        "task_id": execution.task_id,
        "manager_agent_id": execution.manager_agent_id,
        "worker_agent_id": execution.worker_agent_id,
        "status": execution.status,
        "attempt_no": execution.attempt_no,
        "conversation_id": execution.conversation_id,
        "assigned_at": execution.assigned_at,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "result_summary": execution.result_summary,
        "error": execution.error,
        "error_code": execution.error_code,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
    }


def create_agent_tasks_router(
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    agent_store: AgentStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the managed-task router.

    :param task_store: Store for task CRUD and tags.
    :param task_event_store: Store for execution history reads.
    :param agent_store: Used to validate ``manager_agent_id`` references.
    :param auth_provider: Auth provider for owner attribution and access
        checks. ``None`` disables auth enforcement.
    :param permission_store: Used to let admins list/view any task.
        ``None`` disables admin bypass.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    def _is_admin(user_id: str | None) -> bool:
        if user_id is None or permission_store is None:
            return False
        return permission_store.is_admin(user_id)

    def _require_task_access(task: Task, user_id: str | None) -> None:
        if auth_provider is None or user_id is None or _is_admin(user_id):
            return
        if task.owner_user_id is not None and task.owner_user_id != user_id:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)

    def _filter_tasks_for_user(tasks: list[Task], user_id: str | None) -> list[Task]:
        if auth_provider is None or user_id is None or _is_admin(user_id):
            return tasks
        return [
            task
            for task in tasks
            if task.owner_user_id is None or task.owner_user_id == user_id
        ]

    async def _require_manager_agent(manager_agent_id: str) -> None:
        agent = await asyncio.to_thread(agent_store.get, manager_agent_id)
        if agent is None:
            raise OmnigentError(
                f"Manager agent not found: {manager_agent_id!r}",
                code=ErrorCode.NOT_FOUND,
            )

    async def _get_task_or_404(task_id: str, user_id: str | None) -> Task:
        task = await asyncio.to_thread(task_store.get, task_id)
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        _require_task_access(task, user_id)
        return task

    def _tags_from_input(task_id: str, tags: list[TaskTagInput]) -> list[TaskTag]:
        return [
            TaskTag(task_id=task_id, tag_type=tag.tag_type, tag=tag.tag) for tag in tags
        ]

    @router.post("/agent-tasks")
    async def create_task(request: Request, body: CreateAgentTaskRequest) -> dict[str, Any]:
        """Create a managed task."""
        user_id = require_user(request, auth_provider)
        await _require_manager_agent(body.manager_agent_id)
        task_id = _generate_task_id()
        tags = _tags_from_input(task_id, body.tags)
        task = await asyncio.to_thread(
            task_store.create,
            task_id,
            body.manager_agent_id,
            body.title,
            owner_user_id=user_id,
            description=body.description,
            charter=body.charter,
            manager_conversation_id=body.manager_conversation_id,
            state=body.state,
            tags=tags,
        )
        return _task_to_response(task, tags=tags)

    @router.get("/agent-tasks")
    async def list_tasks(
        request: Request,
        state: str | None = None,
        manager_agent_id: str | None = None,
        q: str | None = Query(default=None, min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        """List managed tasks visible to the caller."""
        user_id = get_user_id(request, auth_provider)
        if state is not None and state not in _VALID_TASK_STATES:
            allowed = ", ".join(sorted(_VALID_TASK_STATES))
            raise OmnigentError(
                f"state must be one of: {allowed}",
                code=ErrorCode.INVALID_INPUT,
            )
        if q is not None:
            tasks = await asyncio.to_thread(task_store.search, q, limit=limit)
            if state is not None:
                tasks = [task for task in tasks if task.state == state]
            if manager_agent_id is not None:
                tasks = [task for task in tasks if task.manager_agent_id == manager_agent_id]
        else:
            tasks = await asyncio.to_thread(
                task_store.list,
                state=state,
                manager_agent_id=manager_agent_id,
            )
        tasks = _filter_tasks_for_user(tasks, user_id)[:limit]
        return {
            "object": "list",
            "data": [_task_to_response(task) for task in tasks],
        }

    @router.get("/agent-tasks/{task_id}")
    async def get_task(request: Request, task_id: str) -> dict[str, Any]:
        """Return one managed task with its tags."""
        user_id = get_user_id(request, auth_provider)
        task = await _get_task_or_404(task_id, user_id)
        tags = await asyncio.to_thread(task_store.get_tags, task_id)
        return _task_to_response(task, tags=tags)

    @router.patch("/agent-tasks/{task_id}")
    async def update_task(
        request: Request,
        task_id: str,
        body: UpdateAgentTaskRequest,
    ) -> dict[str, Any]:
        """Update mutable task fields."""
        user_id = require_user(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        update_kwargs: dict[str, Any] = {}
        for field in (
            "title",
            "description",
            "charter",
            "manager_agent_id",
            "manager_conversation_id",
            "state",
        ):
            if field in body.model_fields_set:
                update_kwargs[field] = getattr(body, field)
        if not update_kwargs:
            task = await asyncio.to_thread(task_store.get, task_id)
            assert task is not None
            tags = await asyncio.to_thread(task_store.get_tags, task_id)
            return _task_to_response(task, tags=tags)
        manager_agent_id = update_kwargs.get("manager_agent_id")
        if manager_agent_id is not None:
            await _require_manager_agent(manager_agent_id)
        task = await asyncio.to_thread(task_store.update, task_id, **update_kwargs)
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        tags = await asyncio.to_thread(task_store.get_tags, task_id)
        return _task_to_response(task, tags=tags)

    @router.delete("/agent-tasks/{task_id}")
    async def delete_task(request: Request, task_id: str) -> dict[str, Any]:
        """Archive a managed task (soft delete)."""
        user_id = require_user(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        task = await asyncio.to_thread(task_store.update, task_id, state="archived")
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        return {"id": task_id, "object": "agent.task", "deleted": True, "state": task.state}

    @router.put("/agent-tasks/{task_id}/tags")
    async def put_task_tags(
        request: Request,
        task_id: str,
        body: PutTaskTagsRequest,
    ) -> dict[str, Any]:
        """Replace all tags on a managed task."""
        user_id = require_user(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        tags = _tags_from_input(task_id, body.tags)
        try:
            saved = await asyncio.to_thread(task_store.set_tags, task_id, tags)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.NOT_FOUND) from exc
        task = await asyncio.to_thread(task_store.get, task_id)
        assert task is not None
        return _task_to_response(task, tags=saved)

    @router.get("/agent-tasks/{task_id}/executions")
    async def list_task_executions(request: Request, task_id: str) -> dict[str, Any]:
        """Return worker execution history for a managed task."""
        user_id = get_user_id(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        executions = await asyncio.to_thread(task_event_store.list_executions_for_task, task_id)
        return {
            "object": "list",
            "data": [_execution_to_response(execution) for execution in executions],
        }

    return router
