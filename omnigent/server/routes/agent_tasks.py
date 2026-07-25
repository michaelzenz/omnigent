"""Routes for managed agent tasks (``/v1/agent-tasks``).

Tasks are long-lived work units owned by a manager agent. This router
exposes CRUD for tasks and tags plus read-only execution history. Event
ingress and manager wake are handled in later phases.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from omnigent.agent_tasks.adoption import (
    SESSION_ADOPTION_PROPOSAL,
    adopt_session,
    find_open_adoption_proposal,
    flush_pending_orphan_sessions,
    propose_session_adoption,
    reject_session_adoption,
)
from omnigent.agent_tasks.bootstrap import bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.constants import (
    DEFAULT_SECRETARY_HARNESS,
    DEFAULT_SECRETARY_MODEL,
    DEFAULT_TASK_WORKSPACE,
)
from omnigent.agent_tasks.dashboard import build_task_dashboard
from omnigent.agent_tasks.dispatch import (
    dispatch_worker_for_item,
    resolve_dispatch_params,
)
from omnigent.agent_tasks.fyi_clusters import (
    resolve_fyi_cluster,
    upsert_fyi_cluster,
)
from omnigent.agent_tasks.routing_proposals import (
    build_orphan_inbox,
    list_board_triage,
    resolve_routing_proposal,
    upsert_routing_proposal,
)
from omnigent.agent_tasks.secretary_session import (
    bootstrap_secretary_conversation,
    get_or_create_secretary_profile,
)
from omnigent.agent_tasks.items import (
    create_task_item,
    patch_task_item,
    reconcile_events,
    resolve_task_item,
    submit_item_for_user_ack,
)
from omnigent.db.enum_codecs import TASK_STATE
from omnigent.entities import FyiCluster, Task, TaskEventExecution, TaskItem, TaskTag
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_OWNER, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access, require_user
from omnigent.server.routes.task_events import _event_to_response
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.secretary_profile_store import SecretaryProfileStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
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
    harness: str = DEFAULT_SECRETARY_HARNESS
    model: str = DEFAULT_SECRETARY_MODEL
    host_id: str | None = None
    workspace: str = DEFAULT_TASK_WORKSPACE


class BootstrapTaskManagerRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/bootstrap``."""

    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


class CreateTaskItemRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/items``."""

    title: str
    instructions: str | None = None
    worker_agent_id: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    priority: int = 0
    state: str = "draft"
    canonical_key: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    submit_for_user_ack: bool = False

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped


class ReconcileEventsRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/reconcile``."""

    event_ids: list[str] = Field(min_length=1)

    @field_validator("event_ids")
    @classmethod
    def _non_empty_ids(cls, value: list[str]) -> list[str]:
        cleaned = [event_id.strip() for event_id in value if event_id.strip()]
        if not cleaned:
            raise ValueError("event_ids must contain at least one id")
        return cleaned


class DispatchTaskItemRequest(BaseModel):
    """Request body for ``POST /v1/task-items/{item_id}/dispatch``."""

    worker_agent_id: str | None = None
    title: str | None = None
    instructions: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


class ResolveTaskItemRequest(BaseModel):
    """Request body for ``POST /v1/task-items/{item_id}/resolve``."""

    resolution: Literal["accept_item", "edit_and_dispatch", "reject_item"]
    edited_payload: dict[str, Any] | None = None


class UpdateTaskItemRequest(BaseModel):
    """Request body for ``PATCH /v1/task-items/{item_id}``."""

    title: str | None = None
    instructions: str | None = None
    worker_agent_id: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped


class CreateRoutingProposalRequest(BaseModel):
    """Request body for ``POST /v1/task-items/routing-proposals``."""

    canonical_key: str
    title: str
    event_ids: list[str] = Field(min_length=1)
    recommended_task_id: str
    instructions: str | None = None
    worker_agent_id: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    rationale: str | None = None
    candidates: list[dict[str, Any]] | None = None
    recommend_new_task: bool = False
    proposed_task_id: str | None = None
    proposed_task_title: str | None = None
    proposed_task_charter: str | None = None
    proposed_task_description: str | None = None
    proposed_task_manager_agent_id: str | None = None

    @field_validator("canonical_key", "title", "recommended_task_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must be a non-empty string")
        return stripped

    @field_validator("event_ids")
    @classmethod
    def _non_empty_ids(cls, value: list[str]) -> list[str]:
        cleaned = [event_id.strip() for event_id in value if event_id.strip()]
        if not cleaned:
            raise ValueError("event_ids must contain at least one id")
        return cleaned


class ResolveRoutingProposalRequest(BaseModel):
    """Request body for ``POST /v1/task-items/{item_id}/resolve-routing``."""

    resolution: Literal["accept_routing", "reject_routing"]
    selected_task_id: str | None = None
    instructions: str | None = None
    proposed_task_title: str | None = None
    proposed_task_charter: str | None = None
    proposed_task_description: str | None = None


class CreateFyiClusterRequest(BaseModel):
    """Request body for ``POST /v1/task-events/fyi-clusters``."""

    canonical_key: str
    headline: str
    event_ids: list[str] = Field(min_length=1)
    rationale: str | None = None

    @field_validator("canonical_key", "headline")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must be a non-empty string")
        return stripped

    @field_validator("event_ids")
    @classmethod
    def _non_empty_ids(cls, value: list[str]) -> list[str]:
        cleaned = [event_id.strip() for event_id in value if event_id.strip()]
        if not cleaned:
            raise ValueError("event_ids must contain at least one id")
        return cleaned


class ResolveFyiClusterRequest(BaseModel):
    """Request body for ``POST /v1/fyi-clusters/{cluster_id}/resolve``."""

    resolution: Literal["dismiss_fyi", "promote_to_routing"]
    routing_title: str | None = None
    routing_instructions: str | None = None
    recommended_task_id: str | None = None
    recommend_new_task: bool = False
    proposed_task_title: str | None = None
    proposed_task_charter: str | None = None
    worker_agent_id: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    manager_agent_id: str | None = None


class AdoptSessionRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/sessions/{session_id}/adopt``."""

    task_id: str
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
        "task_item_id": execution.task_item_id,
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


def _item_to_response(item: TaskItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "object": "agent.task.item",
        "task_id": item.task_id,
        "title": item.title,
        "state": item.state,
        "canonical_key": item.canonical_key,
        "instructions": item.instructions,
        "worker_agent_id": item.worker_agent_id,
        "model": item.model,
        "host_id": item.host_id,
        "workspace": item.workspace,
        "harness": item.harness,
        "priority": item.priority,
        "created_by": item.created_by,
        "routing_proposal": item.routing_proposal,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def create_agent_tasks_router(
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
    agent_store: AgentStore,
    conversation_store: ConversationStore | None = None,
    secretary_profile_store: SecretaryProfileStore | None = None,
    host_store: HostStore | None = None,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the managed-task router.

    :param task_store: Store for task CRUD and tags.
    :param task_event_store: Store for execution history reads.
    :param task_item_store: Store for task items and routing proposals.
    :param agent_store: Used to validate ``manager_agent_id`` references.
    :param conversation_store: Used for manager/secretary session bootstrap.
    :param secretary_profile_store: Per-user secretary defaults for bootstrap.
    :param host_store: Used to auto-provision secretary profiles with a default host.
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

    async def _load_secretary_profile(user_id: str | None) -> UserSecretaryProfile:
        if secretary_profile_store is None:
            raise OmnigentError("Secretary profile not found", code=ErrorCode.NOT_FOUND)
        if host_store is None or agent_store is None:
            profile = await asyncio.to_thread(
                secretary_profile_store.get,
                _effective_user_id(user_id),
            )
            if profile is None:
                raise OmnigentError("Secretary profile not found", code=ErrorCode.NOT_FOUND)
            return profile
        return await asyncio.to_thread(
            get_or_create_secretary_profile,
            profile_user_id=_effective_user_id(user_id),
            auth_user_id=user_id,
            secretary_profile_store=secretary_profile_store,
            host_store=host_store,
            agent_store=agent_store,
        )

    if secretary_profile_store is not None:

        @router.get("/agent-tasks/secretary/profile")
        async def get_secretary_profile(request: Request) -> dict[str, Any]:
            """Return the caller's secretary profile."""
            user_id = require_user(request, auth_provider)
            profile = await _load_secretary_profile(user_id)
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
                profile = await _load_secretary_profile(user_id)
                existing = None
                if profile.conversation_id is not None:
                    existing = await asyncio.to_thread(
                        conversation_store.get_conversation,
                        profile.conversation_id,
                    )
                if existing is not None:
                    await flush_pending_orphan_sessions(effective_user_id)
                    return {
                        "object": "agent.task.secretary_session",
                        "conversation_id": existing.id,
                        "created": False,
                    }
                conversation_id = await asyncio.to_thread(
                    bootstrap_secretary_conversation,
                    conversation_store=conversation_store,
                    agent_store=agent_store,
                    profile=profile,
                    seed_manual=True,
                )
                await asyncio.to_thread(
                    secretary_profile_store.upsert,
                    effective_user_id,
                    conversation_id=conversation_id,
                )
                await flush_pending_orphan_sessions(effective_user_id)
                return {
                    "object": "agent.task.secretary_session",
                    "conversation_id": conversation_id,
                    "created": True,
                }

            @router.post("/agent-tasks/secretary/session/reset")
            async def reset_secretary_session(request: Request) -> dict[str, Any]:
                """Delete the current secretary session and create a fresh one."""
                user_id = require_user(request, auth_provider)
                effective_user_id = _effective_user_id(user_id)
                profile = await _load_secretary_profile(user_id)
                if profile.conversation_id is not None:
                    await conversation_store.delete_conversation(profile.conversation_id)
                await asyncio.to_thread(
                    secretary_profile_store.upsert,
                    effective_user_id,
                    harness=DEFAULT_SECRETARY_HARNESS,
                    model=DEFAULT_SECRETARY_MODEL,
                    clear_conversation_id=True,
                )
                profile = await asyncio.to_thread(secretary_profile_store.get, effective_user_id)
                assert profile is not None
                conversation_id = await asyncio.to_thread(
                    bootstrap_secretary_conversation,
                    conversation_store=conversation_store,
                    agent_store=agent_store,
                    profile=profile,
                    seed_manual=True,
                )
                await asyncio.to_thread(
                    secretary_profile_store.upsert,
                    effective_user_id,
                    conversation_id=conversation_id,
                )
                await flush_pending_orphan_sessions(effective_user_id)
                return {
                    "object": "agent.task.secretary_session",
                    "conversation_id": conversation_id,
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
                agent_store=agent_store,
                params=params,
            )
            tags = await asyncio.to_thread(task_store.get_tags, task_id)
            return _task_to_response(bootstrapped, tags=tags)

        @router.get("/agent-tasks/{task_id}/dashboard")
        async def get_task_dashboard(request: Request, task_id: str) -> dict[str, Any]:
            """Return a card-shaped snapshot for one managed task."""
            user_id = get_user_id(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            return await asyncio.to_thread(
                build_task_dashboard,
                task,
                task_event_store,
                task_item_store,
            )

        @router.get("/agent-tasks/{task_id}/items")
        async def list_task_items(
            request: Request,
            task_id: str,
            state: str | None = None,
        ) -> dict[str, Any]:
            """List task items for one managed task."""
            user_id = get_user_id(request, auth_provider)
            await _get_task_or_404(task_id, user_id)
            items = await asyncio.to_thread(
                task_item_store.list_items_for_task,
                task_id,
                state=state,
            )
            return {
                "object": "list",
                "data": [_item_to_response(item) for item in items],
            }

        @router.post("/agent-tasks/{task_id}/items")
        async def create_task_item_route(
            request: Request,
            task_id: str,
            body: CreateTaskItemRequest,
        ) -> dict[str, Any]:
            """Create a task item and optionally link routed events."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)

            def _create() -> TaskItem:
                item = create_task_item(
                    task=task,
                    task_item_store=task_item_store,
                    task_event_store=task_event_store,
                    title=body.title,
                    state=body.state,
                    canonical_key=body.canonical_key,
                    instructions=body.instructions,
                    worker_agent_id=body.worker_agent_id,
                    model=body.model,
                    host_id=body.host_id,
                    workspace=body.workspace,
                    harness=body.harness,
                    priority=body.priority,
                    event_ids=body.event_ids or None,
                )
                if body.submit_for_user_ack and item.state == "draft":
                    return submit_item_for_user_ack(task_item_store, item.id)
                return item

            created = await asyncio.to_thread(_create)
            return _item_to_response(created)

        @router.get("/agent-tasks/{task_id}/reconcile-queue")
        async def get_reconcile_queue(request: Request, task_id: str) -> dict[str, Any]:
            """Return routed events awaiting manager reconcile."""
            user_id = get_user_id(request, auth_provider)
            await _get_task_or_404(task_id, user_id)
            events = await asyncio.to_thread(
                task_event_store.list_events,
                state="routed",
                task_id=task_id,
            )
            return {
                "object": "list",
                "data": [_event_to_response(event) for event in events],
            }

        @router.post("/agent-tasks/{task_id}/reconcile")
        async def reconcile_task_events(
            request: Request,
            task_id: str,
            body: ReconcileEventsRequest,
        ) -> dict[str, Any]:
            """Mark routed events reconciled without creating items."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            reconciled = await asyncio.to_thread(
                reconcile_events,
                task=task,
                event_ids=body.event_ids,
                task_event_store=task_event_store,
            )
            return {
                "object": "list",
                "data": [_event_to_response(event) for event in reconciled],
            }

        async def _get_item_or_404(item_id: str, user_id: str | None) -> TaskItem:
            item = await asyncio.to_thread(task_item_store.get_item, item_id)
            if item is None:
                raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
            await _get_task_or_404(item.task_id, user_id)
            return item

        @router.post("/task-items/{item_id}/resolve")
        async def resolve_task_item_route(
            request: Request,
            item_id: str,
            body: ResolveTaskItemRequest,
        ) -> dict[str, Any]:
            """Accept, edit, or reject a user-inbox task item."""
            user_id = require_user(request, auth_provider)
            item = await _get_item_or_404(item_id, user_id)
            task = await _get_task_or_404(item.task_id, user_id)
            profile = None
            if secretary_profile_store is not None:
                profile = await asyncio.to_thread(
                    secretary_profile_store.get,
                    _effective_user_id(user_id),
                )
            if body.resolution == "edit_and_dispatch" and body.edited_payload is None:
                raise OmnigentError("edited_payload is required", code=ErrorCode.INVALID_INPUT)

            def _resolve() -> tuple[TaskItem, TaskEventExecution | None]:
                return resolve_task_item(
                    item=item,
                    resolution=body.resolution,
                    task=task,
                    task_item_store=task_item_store,
                    task_event_store=task_event_store,
                    conversation_store=conversation_store,
                    edited_payload=body.edited_payload,
                    secretary_profile=profile,
                )

            updated, execution = await asyncio.to_thread(_resolve)
            response = _item_to_response(updated)
            if execution is not None:
                response["execution_id"] = execution.id
                response["worker_conversation_id"] = execution.conversation_id
            return response

        @router.patch("/task-items/{item_id}")
        async def update_task_item_route(
            request: Request,
            item_id: str,
            body: UpdateTaskItemRequest,
        ) -> dict[str, Any]:
            """Update a queued work item before dispatch."""
            user_id = require_user(request, auth_provider)
            item = await _get_item_or_404(item_id, user_id)

            def _patch() -> TaskItem:
                return patch_task_item(
                    item=item,
                    task_item_store=task_item_store,
                    title=body.title,
                    instructions=body.instructions,
                    worker_agent_id=body.worker_agent_id,
                    model=body.model,
                    host_id=body.host_id,
                    workspace=body.workspace,
                    harness=body.harness,
                )

            updated = await asyncio.to_thread(_patch)
            return _item_to_response(updated)

        @router.post("/task-items/{item_id}/dispatch")
        async def dispatch_task_item(
            request: Request,
            item_id: str,
            body: DispatchTaskItemRequest,
        ) -> dict[str, Any]:
            """Dispatch a worker for one task item."""
            user_id = require_user(request, auth_provider)
            item = await _get_item_or_404(item_id, user_id)
            task = await _get_task_or_404(item.task_id, user_id)
            profile = None
            if secretary_profile_store is not None:
                profile = await asyncio.to_thread(
                    secretary_profile_store.get,
                    _effective_user_id(user_id),
                )
            payload = {
                "worker_agent_id": item.worker_agent_id,
                "title": item.title,
                "instructions": item.instructions or "",
                "host_id": item.host_id,
                "workspace": item.workspace,
                "harness": item.harness,
                "model": item.model,
            }
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
                return dispatch_worker_for_item(
                    task=task,
                    item=item,
                    params=params,
                    task_item_store=task_item_store,
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

        @router.get("/task-events/orphan-inbox")
        async def get_orphan_inbox(request: Request) -> dict[str, Any]:
            """Return orphan events and suggested clusters for secretary reconcile."""
            require_user(request, auth_provider)
            return await asyncio.to_thread(
                build_orphan_inbox,
                task_event_store=task_event_store,
                task_item_store=task_item_store,
                task_store=task_store,
            )

        @router.post("/task-items/routing-proposals")
        async def create_routing_proposal_route(
            request: Request,
            body: CreateRoutingProposalRequest,
        ) -> dict[str, Any]:
            """Create or extend a secretary routing proposal over orphan events."""
            user_id = require_user(request, auth_provider)

            def _create() -> TaskItem | None:
                return upsert_routing_proposal(
                    owner_user_id=_effective_user_id(user_id),
                    canonical_key=body.canonical_key,
                    title=body.title,
                    event_ids=body.event_ids,
                    recommended_task_id=body.recommended_task_id,
                    task_store=task_store,
                    task_item_store=task_item_store,
                    task_event_store=task_event_store,
                    instructions=body.instructions,
                    worker_agent_id=body.worker_agent_id,
                    model=body.model,
                    host_id=body.host_id,
                    workspace=body.workspace,
                    harness=body.harness,
                    rationale=body.rationale,
                    candidates=body.candidates,
                    recommend_new_task=body.recommend_new_task,
                    proposed_task_id=body.proposed_task_id,
                    proposed_task_title=body.proposed_task_title,
                    proposed_task_charter=body.proposed_task_charter,
                    proposed_task_description=body.proposed_task_description,
                    proposed_task_manager_agent_id=body.proposed_task_manager_agent_id,
                )

            created = await asyncio.to_thread(_create)
            if created is None:
                raise OmnigentError(
                    "No claimable orphan events for routing proposal",
                    code=ErrorCode.CONFLICT,
                )
            return _item_to_response(created)

        @router.get("/agent-tasks/board/decisions")
        async def list_board_decisions(request: Request) -> dict[str, Any]:
            """List pending board-level routing decisions and FYI clusters."""
            user_id = require_user(request, auth_provider)
            return await asyncio.to_thread(
                list_board_triage,
                owner_user_id=_effective_user_id(user_id),
                task_item_store=task_item_store,
                task_event_store=task_event_store,
                task_store=task_store,
            )

        @router.post("/task-items/{item_id}/resolve-routing")
        async def resolve_routing_proposal_route(
            request: Request,
            item_id: str,
            body: ResolveRoutingProposalRequest,
        ) -> dict[str, Any]:
            """Accept or reject a secretary task-item routing proposal."""
            user_id = require_user(request, auth_provider)
            item = await _get_item_or_404(item_id, user_id)
            profile = None
            if secretary_profile_store is not None:
                profile = await asyncio.to_thread(
                    secretary_profile_store.get,
                    _effective_user_id(user_id),
                )

            updated, execution = await resolve_routing_proposal(
                item=item,
                resolution=body.resolution,
                selected_task_id=body.selected_task_id,
                instructions=body.instructions,
                proposed_task_title=body.proposed_task_title,
                proposed_task_charter=body.proposed_task_charter,
                proposed_task_description=body.proposed_task_description,
                task_store=task_store,
                task_item_store=task_item_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                agent_store=agent_store,
                secretary_profile=profile,
            )
            response = _item_to_response(updated)
            if execution is not None:
                response["execution_id"] = execution.id
                response["worker_conversation_id"] = execution.conversation_id
            return response

        @router.post("/task-events/fyi-clusters")
        async def create_fyi_cluster_route(
            request: Request,
            body: CreateFyiClusterRequest,
        ) -> dict[str, Any]:
            """Create or extend a secretary FYI cluster over orphan events."""
            user_id = require_user(request, auth_provider)

            def _create() -> FyiCluster | None:
                return upsert_fyi_cluster(
                    owner_user_id=_effective_user_id(user_id),
                    canonical_key=body.canonical_key,
                    headline=body.headline,
                    event_ids=body.event_ids,
                    task_item_store=task_item_store,
                    task_event_store=task_event_store,
                    rationale=body.rationale,
                )

            created = await asyncio.to_thread(_create)
            if created is None:
                raise OmnigentError(
                    "No claimable orphan events for FYI cluster",
                    code=ErrorCode.CONFLICT,
                )
            return {
                "object": "agent.task.fyi_cluster",
                "id": created.id,
                "headline": created.headline,
                "rationale": created.rationale,
                "state": created.state,
                "created_at": created.created_at,
            }

        @router.post("/fyi-clusters/{cluster_id}/resolve")
        async def resolve_fyi_cluster_route(
            request: Request,
            cluster_id: str,
            body: ResolveFyiClusterRequest,
        ) -> dict[str, Any]:
            """Dismiss or promote an FYI cluster to a routing decision."""
            user_id = require_user(request, auth_provider)
            cluster = await asyncio.to_thread(task_item_store.get_fyi_cluster, cluster_id)
            if cluster is None or (
                cluster.owner_user_id != _effective_user_id(user_id)
                and not _is_admin(user_id)
            ):
                raise OmnigentError("FYI cluster not found", code=ErrorCode.NOT_FOUND)

            updated, routing_item = await asyncio.to_thread(
                resolve_fyi_cluster,
                cluster=cluster,
                resolution=body.resolution,
                owner_user_id=_effective_user_id(user_id),
                task_store=task_store,
                task_item_store=task_item_store,
                task_event_store=task_event_store,
                routing_title=body.routing_title,
                routing_instructions=body.routing_instructions,
                recommended_task_id=body.recommended_task_id,
                worker_agent_id=body.worker_agent_id,
                model=body.model,
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                manager_agent_id=body.manager_agent_id,
                proposed_task_title=body.proposed_task_title,
                proposed_task_charter=body.proposed_task_charter,
                recommend_new_task=body.recommend_new_task,
            )
            response: dict[str, Any] = {
                "object": "agent.task.fyi_cluster",
                "id": updated.id,
                "state": updated.state,
                "resolved_at": updated.resolved_at,
            }
            if routing_item is not None:
                response["routing_item_id"] = routing_item.id
            return response

        async def _require_session_or_404(session_id: str, user_id: str | None) -> None:
            conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
            if conv is None:
                raise OmnigentError("Session not found", code=ErrorCode.NOT_FOUND)
            await require_access(
                user_id,
                session_id,
                LEVEL_OWNER,
                permission_store,
                conversation_store,
            )

        @router.post("/agent-tasks/sessions/{session_id}/propose-adoption")
        async def propose_session_adoption_route(
            request: Request,
            session_id: str,
        ) -> dict[str, Any]:
            """Score tasks and create a user-gated session adoption proposal."""
            user_id = require_user(request, auth_provider)
            await _require_session_or_404(session_id, user_id)
            created = await asyncio.to_thread(
                propose_session_adoption,
                session_id=session_id,
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                owner_user_id=_effective_user_id(user_id),
            )
            return _event_to_response(created)

        @router.post("/agent-tasks/sessions/{session_id}/adopt")
        async def adopt_session_route(
            request: Request,
            session_id: str,
            body: AdoptSessionRequest,
        ) -> dict[str, Any]:
            """Bind an orphan session to a task after user acceptance."""
            user_id = require_user(request, auth_provider)
            await _require_session_or_404(session_id, user_id)
            await _get_task_or_404(body.task_id, user_id)
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
            proposal = await asyncio.to_thread(
                find_open_adoption_proposal,
                task_event_store,
                session_id,
            )
            runner_router = getattr(request.app.state, "runner_router", None)
            proposal_event, adopted_event = await adopt_session(
                session_id=session_id,
                task_id=body.task_id,
                task_store=task_store,
                task_event_store=task_event_store,
                conversation_store=conversation_store,
                agent_store=agent_store,
                runner_router=runner_router,
                params=params,
                proposal_event=proposal,
            )
            binding = await asyncio.to_thread(task_event_store.get_binding, session_id)
            return {
                "object": "agent.task.session_adoption",
                "session_id": session_id,
                "task_id": body.task_id,
                "binding_kind": binding.binding_kind if binding is not None else None,
                "proposal": (
                    _event_to_response(proposal_event)
                    if proposal_event.event_type == SESSION_ADOPTION_PROPOSAL
                    else None
                ),
                "event": _event_to_response(adopted_event),
            }

        @router.post("/agent-tasks/sessions/{session_id}/reject-adoption")
        async def reject_session_adoption_route(
            request: Request,
            session_id: str,
        ) -> dict[str, Any]:
            """Dismiss adoption for a session that should stay orphan."""
            user_id = require_user(request, auth_provider)
            await _require_session_or_404(session_id, user_id)
            proposal = await asyncio.to_thread(
                find_open_adoption_proposal,
                task_event_store,
                session_id,
            )
            dismissed = await asyncio.to_thread(
                reject_session_adoption,
                session_id=session_id,
                conversation_store=conversation_store,
                task_event_store=task_event_store,
                proposal_event=proposal,
            )
            return {
                "object": "agent.task.session_adoption_rejection",
                "session_id": session_id,
                "proposal": _event_to_response(dismissed) if dismissed is not None else None,
            }

    return router
