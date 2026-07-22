"""Routes for managed agent tasks (``/v1/agent-tasks``).

Tasks are long-lived work units owned by a manager agent. This router
exposes CRUD for tasks and tags plus read-only execution history. Event
ingress and manager wake are handled in later phases.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from omnigent.agent_tasks.bootstrap import bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.constants import DEFAULT_TASK_HARNESS, DEFAULT_TASK_MODEL
from omnigent.agent_tasks.dashboard import build_task_dashboard
from omnigent.agent_tasks.dispatch import (
    dispatch_worker_for_event,
    parse_dispatch_payload,
    resolve_dispatch_params,
)
from omnigent.agent_tasks.event_types import MANAGER_PROPOSAL, MANAGER_WORK_ITEM
from omnigent.agent_tasks.proposals import create_manager_proposal
from omnigent.db.enum_codecs import TASK_STATE
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskEventExecution, TaskTag
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_user
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.secretary_profile_store import SecretaryProfileStore
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


class PutSecretaryProfileRequest(BaseModel):
    """Request body for ``PUT /v1/agent-tasks/secretary/profile``."""

    agent_id: str
    harness: str = DEFAULT_TASK_HARNESS
    model: str = DEFAULT_TASK_MODEL
    host_id: str | None = None
    workspace: str | None = None


class BootstrapTaskManagerRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/bootstrap``."""

    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


class CreateTaskEventRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/events``."""

    event_type: Literal["manager.proposal", "manager.work_item"]
    title: str
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DispatchTaskWorkerRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/dispatch``."""

    event_id: str
    worker_agent_id: str | None = None
    title: str | None = None
    instructions: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


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


def _secretary_profile_to_response(profile: UserSecretaryProfile) -> dict[str, Any]:
    return {
        "object": "agent.task.secretary_profile",
        "user_id": profile.user_id,
        "agent_id": profile.agent_id,
        "conversation_id": profile.conversation_id,
        "harness": profile.harness,
        "model": profile.model,
        "host_id": profile.host_id,
        "workspace": profile.workspace,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


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
    conversation_store: ConversationStore | None = None,
    secretary_profile_store: SecretaryProfileStore | None = None,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the managed-task router.

    :param task_store: Store for task CRUD and tags.
    :param task_event_store: Store for execution history reads.
    :param agent_store: Used to validate ``manager_agent_id`` references.
    :param conversation_store: Used for manager/secretary session bootstrap.
    :param secretary_profile_store: Per-user secretary defaults for bootstrap.
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

    def _effective_user_id(user_id: str | None) -> str:
        return user_id if user_id is not None else "__anonymous__"

    if secretary_profile_store is not None:

        @router.get("/agent-tasks/secretary/profile")
        async def get_secretary_profile(request: Request) -> dict[str, Any]:
            """Return the caller's secretary profile."""
            user_id = require_user(request, auth_provider)
            profile = await asyncio.to_thread(
                secretary_profile_store.get,
                _effective_user_id(user_id),
            )
            if profile is None:
                raise OmnigentError("Secretary profile not found", code=ErrorCode.NOT_FOUND)
            return _secretary_profile_to_response(profile)

        @router.put("/agent-tasks/secretary/profile")
        async def put_secretary_profile(
            request: Request,
            body: PutSecretaryProfileRequest,
        ) -> dict[str, Any]:
            """Create or update the caller's secretary profile."""
            user_id = require_user(request, auth_provider)
            await _require_manager_agent(body.agent_id)
            profile = await asyncio.to_thread(
                secretary_profile_store.upsert,
                _effective_user_id(user_id),
                agent_id=body.agent_id,
                harness=body.harness,
                model=body.model,
                host_id=body.host_id,
                workspace=body.workspace,
            )
            return _secretary_profile_to_response(profile)

        if conversation_store is not None:

            @router.post("/agent-tasks/secretary/session")
            async def ensure_secretary_session(request: Request) -> dict[str, Any]:
                """Ensure the caller has a live secretary session."""
                user_id = require_user(request, auth_provider)
                effective_user_id = _effective_user_id(user_id)
                profile = await asyncio.to_thread(secretary_profile_store.get, effective_user_id)
                if profile is None:
                    raise OmnigentError("Secretary profile not found", code=ErrorCode.NOT_FOUND)
                if profile.conversation_id is not None:
                    existing = await asyncio.to_thread(
                        conversation_store.get_conversation,
                        profile.conversation_id,
                    )
                    if existing is not None:
                        return {
                            "object": "agent.task.secretary_session",
                            "conversation_id": existing.id,
                            "created": False,
                        }
                params = resolve_bootstrap_params(
                    host_id=profile.host_id,
                    workspace=profile.workspace,
                    harness=profile.harness,
                    model=profile.model,
                    secretary_profile=profile,
                )
                conversation = await asyncio.to_thread(
                    conversation_store.create_conversation,
                    title="Task secretary",
                    agent_id=profile.agent_id,
                    host_id=params.host_id,
                    workspace=params.workspace,
                )
                await asyncio.to_thread(
                    conversation_store.update_conversation,
                    conversation.id,
                    harness_override=params.harness,
                    model_override=params.model,
                )
                await asyncio.to_thread(
                    secretary_profile_store.upsert,
                    effective_user_id,
                    conversation_id=conversation.id,
                )
                return {
                    "object": "agent.task.secretary_session",
                    "conversation_id": conversation.id,
                    "created": True,
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

    if conversation_store is not None:

        @router.post("/agent-tasks/{task_id}/bootstrap")
        async def bootstrap_manager(
            request: Request,
            task_id: str,
            body: BootstrapTaskManagerRequest,
        ) -> dict[str, Any]:
            """Bootstrap the manager session for a managed task."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            profile = None
            if secretary_profile_store is not None:
                profile = await asyncio.to_thread(
                    secretary_profile_store.get,
                    _effective_user_id(user_id),
                )
            params = resolve_bootstrap_params(
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                model=body.model,
                secretary_profile=profile,
            )
            bootstrapped = await asyncio.to_thread(
                bootstrap_task_manager,
                task=task,
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                params=params,
            )
            tags = await asyncio.to_thread(task_store.get_tags, task_id)
            return _task_to_response(bootstrapped, tags=tags)

        @router.get("/agent-tasks/{task_id}/dashboard")
        async def get_task_dashboard(request: Request, task_id: str) -> dict[str, Any]:
            """Return a card-shaped snapshot for one managed task."""
            user_id = get_user_id(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            return await asyncio.to_thread(build_task_dashboard, task, task_event_store)

        @router.post("/agent-tasks/{task_id}/events")
        async def create_task_event(
            request: Request,
            task_id: str,
            body: CreateTaskEventRequest,
        ) -> dict[str, Any]:
            """Create a manager-internal task event (proposal or work item)."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            if body.event_type == MANAGER_PROPOSAL:
                created = await asyncio.to_thread(
                    create_manager_proposal,
                    task=task,
                    task_event_store=task_event_store,
                    title=body.title,
                    payload=body.payload,
                    summary=body.summary,
                )
            else:
                event_id = uuid.uuid4().hex
                created = await asyncio.to_thread(
                    task_event_store.create_event,
                    event_id,
                    MANAGER_WORK_ITEM,
                    body.title,
                    task_id=task.id,
                    payload=json.dumps(body.payload),
                    source="manager",
                    summary=body.summary,
                    state="routed",
                    manager_agent_id=task.manager_agent_id,
                    manager_conversation_id=task.manager_conversation_id,
                )
                await asyncio.to_thread(
                    task_event_store.update_event,
                    created.id,
                    routed_at=now_epoch(),
                )
            return {
                "id": created.id,
                "object": "agent.task.event",
                "event_type": created.event_type,
                "title": created.title,
                "state": created.state,
                "task_id": created.task_id,
                "payload": created.payload,
            }

        @router.post("/agent-tasks/{task_id}/dispatch")
        async def dispatch_task_worker(
            request: Request,
            task_id: str,
            body: DispatchTaskWorkerRequest,
        ) -> dict[str, Any]:
            """Dispatch a worker for a routed task event."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            event = await asyncio.to_thread(task_event_store.get_event, body.event_id)
            if event is None:
                raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
            profile = None
            if secretary_profile_store is not None:
                profile = await asyncio.to_thread(
                    secretary_profile_store.get,
                    _effective_user_id(user_id),
                )
            payload = parse_dispatch_payload(event.payload)
            params = resolve_dispatch_params(
                payload=payload,
                worker_agent_id=body.worker_agent_id,
                title=body.title,
                instructions=body.instructions,
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                model=body.model,
                secretary_profile=profile,
            )

            def _dispatch() -> tuple[TaskEventExecution, str]:
                return dispatch_worker_for_event(
                    task=task,
                    event=event,
                    params=params,
                    task_event_store=task_event_store,
                    conversation_store=conversation_store,
                )

            execution, worker_conversation_id = await asyncio.to_thread(_dispatch)
            return {
                "object": "agent.task.dispatch",
                "execution_id": execution.id,
                "conversation_id": worker_conversation_id,
                "status": execution.status,
            }

    return router
