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
from pydantic import BaseModel, Field, field_validator, model_validator

from omnigent.agent_tasks.adoption import (
    SESSION_ADOPTION_PROPOSAL,
    adopt_external_session,
    adopt_session,
    find_open_adoption_proposal,
    find_open_external_adoption_proposal,
    propose_external_session_adoption,
    propose_session_adoption,
    reject_external_session_adoption,
    reject_session_adoption,
)
from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_MANAGER_AGENT_NAME,
    TASK_SECRETARY_ROLE,
    resolve_task_agent_id,
)
from omnigent.agent_tasks.bootstrap import bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.broker_inbox import build_ambiguous_inbox
from omnigent.agent_tasks.broker_session import (
    ensure_role_profile,
    get_or_create_role_profile,
)
from omnigent.agent_tasks.dashboard import build_task_dashboard
from omnigent.agent_tasks.dispatch import (
    dispatch_worker_for_item,
    resolve_dispatch_params,
)
from omnigent.agent_tasks.fyi_clusters import (
    create_fyi_cluster,
    list_fyi_board_cards,
    resolve_fyi_cluster,
)
from omnigent.agent_tasks.items import (
    create_task_item,
    patch_task_item,
    reconcile_events,
    reject_task_item,
    resolve_task_item,
    submit_item_for_user_ack,
)
from omnigent.agent_tasks.manager_role_profile import (
    get_or_create_manager_role_profile,
)
from omnigent.agent_tasks.role_keys import (
    MANAGER_ROLE_PREFIX,
    SYSTEM_ROLE_KEYS,
    WORKER_ROLE_PREFIX,
    is_deletable_role_key,
    is_manager_role_key,
    is_system_role_key,
    is_worker_role_key,
    manager_role_key_from_slug,
    normalize_role_profile_key,
    role_profile_title,
    worker_role_key_from_slug,
)
from omnigent.agent_tasks.task_match import (
    collect_event_tags,
    load_events,
    rank_tasks_for_events,
    ranked_task_payload,
    routable_tasks,
    task_tags_from_event_tags,
)
from omnigent.agent_tasks.task_packages import (
    PackageItemSpec,
    accept_task_package,
    create_task_package,
    reconcile_events_to_task_batch,
    reject_task_package,
)
from omnigent.agent_tasks.worker_role_profile import (
    get_or_create_worker_role_profile,
    load_worker_role_profile,
)
from omnigent.agent_tasks.workers import activate_worker_lane, worker_for_item
from omnigent.db.enum_codecs import TASK_STATE
from omnigent.entities import (
    FyiCluster,
    Task,
    TaskAsset,
    TaskEventExecution,
    TaskItem,
    TaskTag,
    Worker,
)
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_OWNER, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access, require_user
from omnigent.server.routes.task_events import _event_to_response
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.task_asset_store import TaskAssetStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.user_role_session_store import UserRoleSessionStore
from omnigent.stores.worker_store import WorkerStore

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

    title: str
    description: str | None = None
    internal_note: str | None = None
    manager_conversation_id: str | None = None
    state: str = "idle"
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
    internal_note: str | None = None
    manager_role_key: str | None = None
    worker_role_key: str | None = None
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


class _RoleProfileFieldsMixin(BaseModel):
    """Shared role-profile fields with explicit-null handling for ``model``."""

    harness: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None

    @property
    def clears_model(self) -> bool:
        """Whether the caller explicitly sent ``model: null`` to clear it."""
        return "model" in self.model_fields_set and self.model is None


class PutAgentRoleProfileRequest(_RoleProfileFieldsMixin):
    """Request body for ``PUT /v1/agent-tasks/roles/{role}/profile``."""

    agent_profile_id: str


class CreateManagerRoleProfileRequest(_RoleProfileFieldsMixin):
    """Request body for ``POST /v1/agent-tasks/roles/manager``."""

    slug: str
    agent_profile_id: str | None = None


class CreateWorkerRoleProfileRequest(_RoleProfileFieldsMixin):
    """Request body for ``POST /v1/agent-tasks/roles/worker``."""

    slug: str
    agent_profile_id: str | None = None


class BootstrapTaskManagerRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/bootstrap``."""

    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


class CreateTaskItemRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/items``."""

    title: str
    description: str | None = None
    instructions: str | None = None
    internal_note: str | None = None
    worker_role_key: str | None = None
    state: str = "draft"
    event_ids: list[str] = Field(default_factory=list)
    submit_for_user_ack: bool = False

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped


class CreateTaskAssetRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/assets``."""

    kind: Literal["url"] = "url"
    title: str
    url: str

    @field_validator("title", "url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be a non-empty string")
        return stripped


class AckEventsRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/ack``."""

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

    worker_role_key: str | None = None
    instructions: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


class ResolveTaskItemRequest(BaseModel):
    """Request body for ``POST /v1/task-items/{item_id}/resolve``."""

    resolution: Literal["accept_item", "edit_and_dispatch", "reject_item"]
    edited_payload: dict[str, Any] | None = None


class UpdateWorkerLaneRequest(BaseModel):
    """Request body for ``PATCH /v1/task-workers/{worker_id}``."""

    role_key: str


class UpdateTaskItemRequest(BaseModel):
    """Request body for ``PATCH /v1/task-items/{item_id}``."""

    title: str | None = None
    description: str | None = None
    instructions: str | None = None
    internal_note: str | None = None
    worker_role_key: str | None = None

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped


class MatchTasksRequest(BaseModel):
    """Request body for ``POST /v1/task-events/match-tasks``."""

    event_ids: list[str] = Field(min_length=1)

    @field_validator("event_ids")
    @classmethod
    def _non_empty_ids(cls, value: list[str]) -> list[str]:
        cleaned = [event_id.strip() for event_id in value if event_id.strip()]
        if not cleaned:
            raise ValueError("event_ids must contain at least one id")
        return cleaned


class PackageItemInput(BaseModel):
    """One backlog item on a pending task package."""

    title: str
    event_ids: list[str] = Field(min_length=1)
    description: str | None = None
    instructions: str | None = None
    internal_note: str | None = None
    item_id: str | None = None

    @field_validator("title")
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


class CreateTaskPackageRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/packages``."""

    title: str
    description: str | None = None
    internal_note: str | None = None
    tags: list[TaskTagInput] = Field(default_factory=list)
    items: list[PackageItemInput] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must be a non-empty string")
        return stripped


class ReconcileEventsToTaskRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/reconcile-events``.

    Batch by default: pass ``items`` to reconcile multiple items in one call. The
    single-item shorthand (``title`` + ``event_ids`` + optional ``item_id``) is
    accepted for backward compatibility and normalizes to a one-element batch.
    """

    items: list[PackageItemInput] | None = None
    title: str | None = None
    event_ids: list[str] | None = None
    description: str | None = None
    instructions: str | None = None
    internal_note: str | None = None
    item_id: str | None = None
    task_internal_note: str | None = None

    @model_validator(mode="after")
    def _normalize(self) -> ReconcileEventsToTaskRequest:
        if self.items:
            return self
        if self.title is not None and self.event_ids:
            self.items = [
                PackageItemInput(
                    title=self.title,
                    event_ids=self.event_ids,
                    description=self.description,
                    instructions=self.instructions,
                    internal_note=self.internal_note,
                    item_id=self.item_id,
                ),
            ]
            return self
        raise ValueError("Provide `items` (or `title` + `event_ids`)")

    @field_validator("title")
    @classmethod
    def _non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
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


class CreateFyiClusterRequest(BaseModel):
    """Request body for ``POST /v1/task-events/fyi-clusters``."""

    headline: str
    event_ids: list[str] = Field(min_length=1)
    cluster_id: str | None = None
    rationale: str | None = None

    @field_validator("headline")
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
    suggested_task_id: str | None = None
    proposed_task_title: str | None = None
    proposed_task_internal_note: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None


class AdoptSessionRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/sessions/{session_id}/adopt``."""

    task_id: str
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


async def _best_effort_ensure_conversation_runner(
    request: Request,
    conversation_id: str,
    conversation_store: ConversationStore,
) -> None:
    """Launch or reconnect a session runner when possible."""
    from omnigent.server.routes.sessions import (
        ServerRunnerInfrastructure,
        _ensure_runner_session_initialized,
        _server_runner_router,
        ensure_session_runner_client,
    )

    conv = await asyncio.to_thread(conversation_store.get_conversation, conversation_id)
    if conv is None:
        return
    infrastructure = ServerRunnerInfrastructure(
        host_registry=getattr(request.app.state, "host_registry", None),
        tunnel_registry=getattr(request.app.state, "tunnel_registry", None),
        runner_exit_reports=getattr(request.app.state, "runner_exit_reports", None),
    )
    runner_client, needs_session_init = await ensure_session_runner_client(
        conversation_id,
        conv,
        conversation_store=conversation_store,
        runner_router=_server_runner_router,
        infrastructure=infrastructure,
    )
    if runner_client is None:
        return
    if needs_session_init:
        refreshed = await asyncio.to_thread(
            conversation_store.get_conversation,
            conversation_id,
        )
        if refreshed is not None:
            await _ensure_runner_session_initialized(
                conversation_id,
                refreshed,
                runner_client,
                conversation_store,
            )


def _tag_to_response(tag: TaskTag) -> dict[str, str]:
    return {"tag_type": tag.tag_type, "tag": tag.tag}


def _worker_to_response(worker: Worker) -> dict[str, Any]:
    return {
        "object": "agent.task.worker",
        "id": worker.id,
        "task_id": worker.task_id,
        "kind": worker.kind,
        "role_key": worker.role_key,
        "agent_profile_id": worker.agent_profile_id,
        "session_id": worker.session_id,
    }


def _task_to_response(task: Task, *, tags: list[TaskTag] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": task.id,
        "object": "agent.task",
        "manager_role_key": task.manager_role_key,
        "worker_role_key": task.worker_role_key,
        "manager_conversation_id": task.manager_conversation_id,
        "owner_user_id": task.owner_user_id,
        "title": task.title,
        "description": task.description,
        "internal_note": task.internal_note,
        "state": task.state,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }
    if tags is not None:
        result["tags"] = [_tag_to_response(tag) for tag in tags]
    return result


def _require_task_agent_role(role: str) -> str:
    return normalize_role_profile_key(role)


# Roles that own a long-lived session the user can bootstrap on demand. The
# broker triages events; the secretary is a lightweight Q&A assistant. Both
# get a seeded conversation via their own bootstrap helper.
_SESSION_SUPPORTED_ROLES = frozenset({TASK_BROKER_ROLE, TASK_SECRETARY_ROLE})


def _require_session_supported_role(role: str) -> None:
    if role not in _SESSION_SUPPORTED_ROLES:
        raise OmnigentError(
            f"session bootstrap for role {role!r} is not supported yet",
            code=ErrorCode.INVALID_INPUT,
        )


def _role_session_labels(role: str, harness: str) -> dict[str, str]:
    """Build the labels dict for a role session (role + native presentation)."""
    from omnigent.agent_tasks.constants import resolve_task_harness
    from omnigent.agent_tasks.session_labels import (
        BROKER_ROLE_VALUE,
        ROLE_LABEL,
        SECRETARY_ROLE_VALUE,
    )
    from omnigent.native_coding_agents import native_coding_agent_for_harness

    if role == TASK_BROKER_ROLE:
        labels = {ROLE_LABEL: BROKER_ROLE_VALUE}
    elif role == TASK_SECRETARY_ROLE:
        labels = {ROLE_LABEL: SECRETARY_ROLE_VALUE}
    else:
        labels = {}
    native_agent = native_coding_agent_for_harness(resolve_task_harness(harness))
    if native_agent is not None:
        labels.update(native_agent.presentation_labels)
    return labels


async def _create_role_session_via_create_path(
    role: str,
    *,
    profile,
    request: Request,
    user_id: str | None,
    session_creator: Any,
    conversation_store: ConversationStore,
    parent_session_id: str | None = None,
    sub_agent_name: str | None = None,
    title: str | None = None,
    seed_prompt: bool = True,
) -> str:
    """Create a role session through the same ``POST /v1/sessions`` path.

    Builds a ``SessionCreateRequest`` from the glossary profile, calls
    ``session_creator`` (which wraps ``create_session_internal``), then
    layers on role-specific labels passed in the request body so the
    create path applies them atomically.
    """
    from omnigent.agent_tasks.bootstrap import build_role_session_request

    role_title = title or ("Task broker" if role == TASK_BROKER_ROLE else "Task secretary")
    labels = _role_session_labels(role, profile.harness or "")
    body = build_role_session_request(
        profile,
        title=role_title,
        labels=labels,
        parent_session_id=parent_session_id,
        sub_agent_name=sub_agent_name,
    )
    resp = await session_creator(
        body=body,
        request=request,
        user_id=user_id,
    )
    return resp.id


def _agent_role_profile_to_response(
    role: str,
    profile: TaskRoleProfile,
    *,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "object": "agent.task.role_profile",
        "role": role,
        "title": role_profile_title(role),
        "kind": profile.kind,
        "system": is_system_role_key(role),
        "deletable": is_deletable_role_key(role),
        "agent_profile_id": profile.agent_profile_id,
        "conversation_id": conversation_id,
        "harness": profile.harness,
        "model": profile.model,
        "host_id": profile.host_id,
        "workspace": profile.workspace,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _agent_role_session_to_response(
    role: str,
    *,
    conversation_id: str,
    created: bool,
) -> dict[str, Any]:
    return {
        "object": "agent.task.role_session",
        "role": role,
        "conversation_id": conversation_id,
        "created": created,
    }


def _execution_to_response(execution: TaskEventExecution) -> dict[str, Any]:
    return {
        "id": execution.id,
        "object": "agent.task.execution",
        "task_item_id": execution.task_item_id,
        "task_id": execution.task_id,
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


def _asset_to_response(asset: TaskAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "object": "agent.task.asset",
        "task_id": asset.task_id,
        "kind": asset.kind,
        "title": asset.title,
        "url": asset.url,
        "created_at": asset.created_at,
    }


def _item_to_response(item: TaskItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "object": "agent.task.item",
        "task_id": item.task_id,
        "title": item.title,
        "state": item.state,
        "description": item.description,
        "instructions": item.instructions,
        "internal_note": item.internal_note,
        "worker_id": item.worker_id,
        "created_by": item.created_by,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def create_agent_tasks_router(
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
    worker_store: WorkerStore,
    task_asset_store: TaskAssetStore,
    agent_store: AgentStore,
    conversation_store: ConversationStore | None = None,
    task_role_profile_store: TaskRoleProfileStore | None = None,
    user_role_session_store: UserRoleSessionStore | None = None,
    host_store: HostStore | None = None,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
    agent_queue_store: AgentQueueStore | None = None,
    session_creator: Any | None = None,
) -> APIRouter:
    """Build the managed-task router.

    :param task_store: Store for task CRUD and tags.
    :param task_event_store: Store for execution history reads.
    :param task_item_store: Store for task items and routing proposals.
    :param agent_store: Used to resolve the built-in task-manager agent.
    :param conversation_store: Used for manager/broker session bootstrap.
    :param task_role_profile_store: Glossary role definitions used for bootstrap.
    :param user_role_session_store: Per-user session bindings for singleton roles.
    :param host_store: Used to auto-provision role profiles with a default host.
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
            task for task in tasks if task.owner_user_id is None or task.owner_user_id == user_id
        ]

    async def _require_agent_profile(agent_profile_id: str | None) -> None:
        if agent_profile_id is None:
            raise OmnigentError("Agent profile is required", code=ErrorCode.INVALID_INPUT)
        agent = await asyncio.to_thread(agent_store.get, agent_profile_id)
        if agent is None:
            raise OmnigentError(
                f"Agent profile not found: {agent_profile_id!r}",
                code=ErrorCode.NOT_FOUND,
            )

    async def _get_task_or_404(task_id: str, user_id: str | None) -> Task:
        task = await asyncio.to_thread(task_store.get, task_id)
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        _require_task_access(task, user_id)
        return task

    def _tags_from_input(task_id: str, tags: list[TaskTagInput]) -> list[TaskTag]:
        return [TaskTag(task_id=task_id, tag_type=tag.tag_type, tag=tag.tag) for tag in tags]

    @router.post("/agent-tasks")
    async def create_task(request: Request, body: CreateAgentTaskRequest) -> dict[str, Any]:
        """Create a managed task."""
        user_id = require_user(request, auth_provider)
        task_id = _generate_task_id()
        tags = _tags_from_input(task_id, body.tags)
        task = await asyncio.to_thread(
            task_store.create,
            task_id,
            body.title,
            owner_user_id=user_id,
            description=body.description,
            internal_note=body.internal_note,
            manager_conversation_id=body.manager_conversation_id,
            state=body.state,
            tags=tags,
        )
        return _task_to_response(task, tags=tags)

    @router.get("/agent-tasks")
    async def list_tasks(
        request: Request,
        state: str | None = None,
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
        tasks = await asyncio.to_thread(
            task_store.list,
            state=state,
        )
        tasks = _filter_tasks_for_user(tasks, user_id)[:limit]
        return {
            "object": "list",
            "data": [_task_to_response(task) for task in tasks],
        }

    def _effective_user_id(user_id: str | None) -> str:
        return user_id if user_id is not None else "__anonymous__"

    async def _manager_role_profile_for_task(
        task: Task,
        user_id: str | None,
    ) -> TaskRoleProfile | None:
        if task_role_profile_store is None or host_store is None:
            return None
        return await asyncio.to_thread(
            get_or_create_manager_role_profile,
            task_role_profile_store=task_role_profile_store,
            host_store=host_store,
            agent_store=agent_store,
            auth_user_id=user_id,
            task=task,
        )

    async def _worker_role_profile_for_task(
        task: Task,
        user_id: str | None,
        worker: Worker | None = None,
    ) -> TaskRoleProfile | None:
        if task_role_profile_store is None:
            return None
        existing = await asyncio.to_thread(
            load_worker_role_profile,
            task_role_profile_store,
            task,
            worker,
        )
        if existing is not None:
            return existing
        if host_store is None or agent_store is None:
            return None
        return await asyncio.to_thread(
            get_or_create_worker_role_profile,
            task_role_profile_store=task_role_profile_store,
            host_store=host_store,
            agent_store=agent_store,
            auth_user_id=user_id,
            task=task,
            worker=worker,
        )

    async def _ensure_system_role_profiles(user_id: str | None) -> None:
        if task_role_profile_store is None or agent_store is None:
            return
        for role in SYSTEM_ROLE_KEYS:
            await asyncio.to_thread(
                ensure_role_profile,
                role=role,
                auth_user_id=user_id,
                task_role_profile_store=task_role_profile_store,
                agent_store=agent_store,
                host_store=host_store,
            )

    async def _bind_role_session(
        effective_user_id: str,
        role: str,
        conversation_id: str | None,
    ) -> None:
        if user_role_session_store is None:
            return
        await asyncio.to_thread(
            user_role_session_store.set_conversation,
            effective_user_id,
            role,
            conversation_id,
        )

    async def _role_conversation_id(role: str, user_id: str | None) -> str | None:
        if user_role_session_store is None:
            return None
        session = await asyncio.to_thread(
            user_role_session_store.get,
            _effective_user_id(user_id),
            role,
        )
        return session.conversation_id if session is not None else None

    async def _load_role_profile(role: str, user_id: str | None) -> TaskRoleProfile:
        if task_role_profile_store is None:
            raise OmnigentError("Task role profile not found", code=ErrorCode.NOT_FOUND)
        if host_store is None or agent_store is None:
            profile = await asyncio.to_thread(task_role_profile_store.get, role)
            if profile is None:
                raise OmnigentError("Task role profile not found", code=ErrorCode.NOT_FOUND)
            return profile
        return await asyncio.to_thread(
            get_or_create_role_profile,
            role=role,
            auth_user_id=user_id,
            task_role_profile_store=task_role_profile_store,
            host_store=host_store,
            agent_store=agent_store,
        )

    if task_role_profile_store is not None:

        @router.get("/agent-tasks/roles/profiles")
        async def list_agent_role_profiles(
            request: Request,
            prefix: str | None = Query(default=None),
        ) -> dict[str, Any]:
            """List the caller's glossary role profiles."""
            user_id = require_user(request, auth_provider)
            await _ensure_system_role_profiles(user_id)
            profiles = await asyncio.to_thread(task_role_profile_store.list_roles)
            if prefix is not None:
                profiles = [p for p in profiles if p.role.startswith(prefix)]
            return {
                "object": "list",
                "data": [
                    _agent_role_profile_to_response(
                        profile.role,
                        profile,
                        conversation_id=await _role_conversation_id(profile.role, user_id),
                    )
                    for profile in profiles
                ],
            }

        @router.post("/agent-tasks/roles/manager")
        async def create_manager_role_profile(
            request: Request,
            body: CreateManagerRoleProfileRequest,
        ) -> dict[str, Any]:
            """Create a custom manager glossary profile."""
            user_id = require_user(request, auth_provider)
            if agent_store is None:
                raise OmnigentError("Task role profile not found", code=ErrorCode.NOT_FOUND)
            role = manager_role_key_from_slug(body.slug)
            existing = await asyncio.to_thread(task_role_profile_store.get, role)
            if existing is not None:
                raise OmnigentError(
                    f"Manager role already exists: {role}",
                    code=ErrorCode.CONFLICT,
                )
            agent_profile_id = body.agent_profile_id or await asyncio.to_thread(
                resolve_task_agent_id,
                agent_store,
                TASK_MANAGER_AGENT_NAME,
            )
            await _require_agent_profile(agent_profile_id)
            await asyncio.to_thread(
                ensure_role_profile,
                role=role,
                auth_user_id=user_id,
                task_role_profile_store=task_role_profile_store,
                agent_store=agent_store,
                host_store=host_store,
            )
            profile = await asyncio.to_thread(
                task_role_profile_store.upsert,
                role,
                agent_profile_id=agent_profile_id,
                harness=body.harness,
                model=body.model,
                clear_model=body.clears_model,
                host_id=body.host_id,
                workspace=body.workspace,
            )
            return _agent_role_profile_to_response(role, profile)

        @router.post("/agent-tasks/roles/worker")
        async def create_worker_role_profile(
            request: Request,
            body: CreateWorkerRoleProfileRequest,
        ) -> dict[str, Any]:
            """Create a custom worker glossary profile."""
            user_id = require_user(request, auth_provider)
            if agent_store is None:
                raise OmnigentError("Task role profile not found", code=ErrorCode.NOT_FOUND)
            from omnigent.agent_tasks.agent_builtins import TASK_WORKER_AGENT_NAME

            role = worker_role_key_from_slug(body.slug)
            existing = await asyncio.to_thread(task_role_profile_store.get, role)
            if existing is not None:
                raise OmnigentError(
                    f"Worker role already exists: {role}",
                    code=ErrorCode.CONFLICT,
                )
            agent_profile_id = body.agent_profile_id or await asyncio.to_thread(
                resolve_task_agent_id,
                agent_store,
                TASK_WORKER_AGENT_NAME,
            )
            await _require_agent_profile(agent_profile_id)
            await asyncio.to_thread(
                ensure_role_profile,
                role=role,
                auth_user_id=user_id,
                task_role_profile_store=task_role_profile_store,
                agent_store=agent_store,
                host_store=host_store,
            )
            profile = await asyncio.to_thread(
                task_role_profile_store.upsert,
                role,
                agent_profile_id=agent_profile_id,
                harness=body.harness,
                model=body.model,
                clear_model=body.clears_model,
                host_id=body.host_id,
                workspace=body.workspace,
            )
            return _agent_role_profile_to_response(role, profile)

        @router.delete("/agent-tasks/roles/{role}")
        async def delete_agent_role_profile(request: Request, role: str) -> dict[str, Any]:
            """Delete a custom manager or worker glossary profile."""
            role = _require_task_agent_role(role)
            if not is_deletable_role_key(role):
                raise OmnigentError(
                    f"Role profile cannot be deleted: {role}",
                    code=ErrorCode.CONFLICT,
                )
            require_user(request, auth_provider)
            if is_manager_role_key(role):
                in_use = await asyncio.to_thread(
                    task_store.count_by_manager_role_key,
                    role,
                    state="pending",
                )
                conflict_message = "Manager role is assigned to pending tasks"
            elif is_worker_role_key(role):
                in_use = await asyncio.to_thread(
                    task_store.count_by_worker_role_key,
                    role,
                    state="pending",
                )
                conflict_message = "Worker role is assigned to pending tasks"
            else:
                in_use = 0
                conflict_message = "Role is assigned to pending tasks"
            if in_use > 0:
                raise OmnigentError(
                    conflict_message,
                    code=ErrorCode.CONFLICT,
                )
            deleted = await asyncio.to_thread(task_role_profile_store.delete, role)
            if not deleted:
                raise OmnigentError("Task role profile not found", code=ErrorCode.NOT_FOUND)
            return {"object": "agent.task.role_profile", "role": role, "deleted": True}

        @router.get("/agent-tasks/roles/{role}/profile")
        async def get_agent_role_profile(request: Request, role: str) -> dict[str, Any]:
            """Return the caller's profile for a managed task agent role."""
            role = _require_task_agent_role(role)
            user_id = require_user(request, auth_provider)
            profile = await _load_role_profile(role, user_id)
            return _agent_role_profile_to_response(
                role,
                profile,
                conversation_id=await _role_conversation_id(role, user_id),
            )

        @router.put("/agent-tasks/roles/{role}/profile")
        async def put_agent_role_profile(
            request: Request,
            role: str,
            body: PutAgentRoleProfileRequest,
        ) -> dict[str, Any]:
            """Create or update the caller's profile for a managed task agent role."""
            role = _require_task_agent_role(role)
            user_id = require_user(request, auth_provider)
            await _require_agent_profile(body.agent_profile_id)
            profile = await asyncio.to_thread(
                task_role_profile_store.upsert,
                role,
                agent_profile_id=body.agent_profile_id,
                harness=body.harness,
                model=body.model,
                clear_model=body.clears_model,
                host_id=body.host_id,
                workspace=body.workspace,
            )
            return _agent_role_profile_to_response(
                role,
                profile,
                conversation_id=await _role_conversation_id(role, user_id),
            )

        if conversation_store is not None:

            @router.post("/agent-tasks/roles/{role}/session")
            async def ensure_agent_role_session(
                request: Request,
                role: str,
            ) -> dict[str, Any]:
                """Ensure the caller has a live session for a managed task agent role."""
                role = _require_task_agent_role(role)
                _require_session_supported_role(role)
                user_id = require_user(request, auth_provider)
                effective_user_id = _effective_user_id(user_id)
                profile = await _load_role_profile(role, user_id)
                bound_conversation_id = await _role_conversation_id(role, user_id)
                existing = None
                if bound_conversation_id is not None:
                    existing = await asyncio.to_thread(
                        conversation_store.get_conversation,
                        bound_conversation_id,
                    )
                if existing is not None:
                    await _best_effort_ensure_conversation_runner(
                        request,
                        existing.id,
                        conversation_store,
                    )
                    return _agent_role_session_to_response(
                        role,
                        conversation_id=existing.id,
                        created=False,
                    )
                conversation_id = await _create_role_session_via_create_path(
                    role,
                    profile=profile,
                    request=request,
                    user_id=user_id,
                    session_creator=session_creator,
                    conversation_store=conversation_store,
                )
                await _bind_role_session(effective_user_id, role, conversation_id)
                return _agent_role_session_to_response(
                    role,
                    conversation_id=conversation_id,
                    created=True,
                )

            @router.post("/agent-tasks/roles/{role}/session/reset")
            async def reset_agent_role_session(
                request: Request,
                role: str,
            ) -> dict[str, Any]:
                """Delete the current role session and create a fresh one."""
                role = _require_task_agent_role(role)
                _require_session_supported_role(role)
                user_id = require_user(request, auth_provider)
                effective_user_id = _effective_user_id(user_id)
                profile = await _load_role_profile(role, user_id)
                bound_conversation_id = await _role_conversation_id(role, user_id)
                if bound_conversation_id is not None:
                    await conversation_store.delete_conversation(bound_conversation_id)
                await _bind_role_session(effective_user_id, role, None)
                conversation_id = await _create_role_session_via_create_path(
                    role,
                    profile=profile,
                    request=request,
                    user_id=user_id,
                    session_creator=session_creator,
                    conversation_store=conversation_store,
                )
                await _bind_role_session(effective_user_id, role, conversation_id)
                # Orphan sessions are now durable ``session.orphan`` events the
                # broker packager polls, so there is nothing to flush here.
                return _agent_role_session_to_response(
                    role,
                    conversation_id=conversation_id,
                    created=True,
                )

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
        task = await _get_task_or_404(task_id, user_id)
        update_kwargs: dict[str, Any] = {}
        for field in (
            "title",
            "description",
            "internal_note",
            "manager_conversation_id",
            "state",
        ):
            if field in body.model_fields_set:
                update_kwargs[field] = getattr(body, field)
        manager_role_key = None
        if "manager_role_key" in body.model_fields_set:
            if task.state != "pending":
                raise OmnigentError(
                    "manager_role_key can only be changed while the task is pending",
                    code=ErrorCode.CONFLICT,
                )
            manager_role_key = normalize_role_profile_key(body.manager_role_key or "")
            if not manager_role_key.startswith(MANAGER_ROLE_PREFIX):
                raise OmnigentError(
                    "manager_role_key must be a manager glossary role",
                    code=ErrorCode.INVALID_INPUT,
                )
            if task_role_profile_store is not None:
                profile = await asyncio.to_thread(
                    task_role_profile_store.get,
                    manager_role_key,
                )
                if profile is None:
                    raise OmnigentError(
                        f"Task role profile not found: {manager_role_key}",
                        code=ErrorCode.NOT_FOUND,
                    )
            update_kwargs["manager_role_key"] = manager_role_key
        if "worker_role_key" in body.model_fields_set:
            if task.state != "pending":
                raise OmnigentError(
                    "worker_role_key can only be changed while the task is pending",
                    code=ErrorCode.CONFLICT,
                )
            worker_role_key = normalize_role_profile_key(body.worker_role_key or "")
            if not worker_role_key.startswith(WORKER_ROLE_PREFIX):
                raise OmnigentError(
                    "worker_role_key must be a worker glossary role",
                    code=ErrorCode.INVALID_INPUT,
                )
            if task_role_profile_store is not None:
                profile = await asyncio.to_thread(
                    task_role_profile_store.get,
                    worker_role_key,
                )
                if profile is None:
                    raise OmnigentError(
                        f"Task role profile not found: {worker_role_key}",
                        code=ErrorCode.NOT_FOUND,
                    )
            update_kwargs["worker_role_key"] = worker_role_key
        if not update_kwargs:
            task = await asyncio.to_thread(task_store.get, task_id)
            assert task is not None
            tags = await asyncio.to_thread(task_store.get_tags, task_id)
            return _task_to_response(task, tags=tags)
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
            profile = await _manager_role_profile_for_task(task, user_id)
            params = resolve_bootstrap_params(
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                model=body.model,
                role_profile=profile,
            )
            bootstrapped = await bootstrap_task_manager(
                task=task,
                task_store=task_store,
                conversation_store=conversation_store,
                params=params,
                session_creator=session_creator,
                app_state=request.app.state,
                user_id=user_id,
            )
            return _task_to_response(bootstrapped)

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
                worker_store,
                task_asset_store,
            )

        @router.patch("/task-workers/{worker_id}")
        async def update_worker_lane_role(
            request: Request,
            worker_id: str,
            body: UpdateWorkerLaneRequest,
        ) -> dict[str, Any]:
            """Point one worker lane at a different worker role."""
            user_id = get_user_id(request, auth_provider)
            worker = await asyncio.to_thread(worker_store.get_worker, worker_id)
            if worker is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            await _get_task_or_404(worker.task_id, user_id)
            role = _require_task_agent_role(body.role_key)
            if not is_worker_role_key(role):
                raise OmnigentError(
                    f"Not a worker role: {role}",
                    code=ErrorCode.INVALID_INPUT,
                )
            # A lane that already ran keeps its history under the old role.
            if worker.session_id is not None:
                raise OmnigentError(
                    "Worker lane already has a session; its role is fixed",
                    code=ErrorCode.CONFLICT,
                )
            updated = await asyncio.to_thread(worker_store.update_worker, worker_id, role_key=role)
            if updated is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            return _worker_to_response(updated)

        @router.post("/task-workers/{worker_id}/activate")
        async def activate_worker_lane_route(
            request: Request,
            worker_id: str,
        ) -> dict[str, Any]:
            """Start a worker sub-agent session for a lane that has not run yet."""
            user_id = require_user(request, auth_provider)
            worker = await asyncio.to_thread(worker_store.get_worker, worker_id)
            if worker is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            task = await _get_task_or_404(worker.task_id, user_id)
            manager_profile = await _manager_role_profile_for_task(task, user_id)
            worker_profile = await _worker_role_profile_for_task(task, user_id, worker)

            activated, _conversation_id = await activate_worker_lane(
                task=task,
                worker=worker,
                task_store=task_store,
                worker_store=worker_store,
                conversation_store=conversation_store,
                manager_role_profile=manager_profile,
                worker_role_profile=worker_profile,
                session_creator=session_creator,
                app_state=request.app.state,
                user_id=user_id,
            )
            if activated.session_id is not None:
                await _best_effort_ensure_conversation_runner(
                    request,
                    activated.session_id,
                    conversation_store,
                )
            return _worker_to_response(activated)

        @router.post("/agent-tasks/{task_id}/assets")
        async def create_task_asset_route(
            request: Request,
            task_id: str,
            body: CreateTaskAssetRequest,
        ) -> dict[str, Any]:
            """Attach a URL or other asset reference to one managed task."""
            user_id = require_user(request, auth_provider)
            await _get_task_or_404(task_id, user_id)

            def _create() -> TaskAsset:
                return task_asset_store.create_asset(
                    task_id,
                    kind=body.kind,
                    title=body.title,
                    url=body.url,
                )

            created = await asyncio.to_thread(_create)
            return _asset_to_response(created)

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
                    worker_store=worker_store,
                    task_event_store=task_event_store,
                    title=body.title,
                    state=body.state,
                    description=body.description,
                    instructions=body.instructions,
                    internal_note=body.internal_note,
                    worker_role_key=body.worker_role_key,
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

        @router.post("/agent-tasks/{task_id}/ack")
        async def ack_task_events(
            request: Request,
            task_id: str,
            body: AckEventsRequest,
        ) -> dict[str, Any]:
            """Ack routed events as processed without creating items."""
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
            # Rejection cancels the item outright — no manager profile, dispatch
            # params, or worker runner are needed, so short-circuit before them.
            if body.resolution == "reject_item":
                updated = await asyncio.to_thread(
                    reject_task_item,
                    item=item,
                    task_item_store=task_item_store,
                )
                return _item_to_response(updated)
            task = await _get_task_or_404(item.task_id, user_id)
            worker = worker_for_item(item, worker_store=worker_store)
            worker_profile = await _worker_role_profile_for_task(task, user_id, worker)
            manager_profile = await _manager_role_profile_for_task(task, user_id)
            if body.resolution == "edit_and_dispatch" and body.edited_payload is None:
                raise OmnigentError("edited_payload is required", code=ErrorCode.INVALID_INPUT)

            updated, execution = await resolve_task_item(
                item=item,
                resolution=body.resolution,
                task=task,
                task_store=task_store,
                task_item_store=task_item_store,
                task_event_store=task_event_store,
                worker_store=worker_store,
                conversation_store=conversation_store,
                edited_payload=body.edited_payload,
                role_profile=worker_profile or manager_profile,
                agent_queue_store=agent_queue_store,
                owner_user_id=_effective_user_id(user_id),
                session_creator=session_creator,
                app_state=request.app.state,
                user_id=user_id,
            )
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
                    worker_store=worker_store,
                    title=body.title,
                    description=body.description,
                    instructions=body.instructions,
                    internal_note=body.internal_note,
                    worker_role_key=body.worker_role_key,
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
            manager_profile = await _manager_role_profile_for_task(task, user_id)
            worker = worker_for_item(item, worker_store=worker_store)
            worker_profile = await _worker_role_profile_for_task(task, user_id, worker)
            payload = {
                "worker_role_key": body.worker_role_key
                or (worker.role_key if worker is not None else None),
                "instructions": item.instructions or "",
                "internal_note": item.internal_note,
            }
            params = resolve_dispatch_params(
                payload=payload,
                instructions=body.instructions,
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                model=body.model,
                role_profile=worker_profile or manager_profile,
            )

            execution, worker_conversation_id = await dispatch_worker_for_item(
                task=task,
                item=item,
                params=params,
                task_store=task_store,
                task_item_store=task_item_store,
                task_event_store=task_event_store,
                worker_store=worker_store,
                conversation_store=conversation_store,
                session_creator=session_creator,
                app_state=request.app.state,
                user_id=user_id,
            )
            return {
                "object": "agent.task.dispatch",
                "execution_id": execution.id,
                "conversation_id": worker_conversation_id,
                "status": execution.status,
            }

        @router.get("/task-events/ambiguous-inbox")
        async def get_ambiguous_inbox(request: Request) -> dict[str, Any]:
            """Return ambiguous events and suggested clusters for broker reconcile."""
            require_user(request, auth_provider)
            return await asyncio.to_thread(
                build_ambiguous_inbox,
                task_event_store=task_event_store,
                task_item_store=task_item_store,
                task_store=task_store,
            )

        @router.post("/task-events/match-tasks")
        async def match_tasks(request: Request, body: MatchTasksRequest) -> dict[str, Any]:
            """Rank active and pending tasks against one or more events."""
            require_user(request, auth_provider)

            def _match() -> dict[str, Any]:
                events = load_events(body.event_ids, task_event_store=task_event_store)
                ranked = rank_tasks_for_events(
                    events=events,
                    tasks=routable_tasks(task_store),
                    task_store=task_store,
                )
                return {
                    "object": "agent.task.match",
                    "event_ids": body.event_ids,
                    "candidates": ranked_task_payload(ranked),
                }

            return await asyncio.to_thread(_match)

        @router.post("/agent-tasks/packages")
        async def create_task_package_route(
            request: Request,
            body: CreateTaskPackageRequest,
        ) -> dict[str, Any]:
            """Create a pending task package with broker-reconciled items."""
            user_id = require_user(request, auth_provider)
            task_id = _generate_task_id()
            tags = _tags_from_input(task_id, body.tags)
            all_event_ids = [event_id for item in body.items for event_id in item.event_ids]
            event_tags = collect_event_tags(
                all_event_ids,
                task_event_store=task_event_store,
            )

            def _create() -> Task:
                return create_task_package(
                    task_id=task_id,
                    owner_user_id=_effective_user_id(user_id),
                    title=body.title,
                    description=body.description,
                    internal_note=body.internal_note,
                    tags=tags or task_tags_from_event_tags(task_id, event_tags),
                    event_tags=event_tags,
                    items=[
                        PackageItemSpec(
                            title=item.title,
                            event_ids=item.event_ids,
                            description=item.description,
                            instructions=item.instructions,
                            internal_note=item.internal_note,
                            item_id=item.item_id,
                        )
                        for item in body.items
                    ],
                    task_store=task_store,
                    task_item_store=task_item_store,
                    task_event_store=task_event_store,
                    worker_store=worker_store,
                )

            task = await asyncio.to_thread(_create)
            saved_tags = await asyncio.to_thread(task_store.get_tags, task.id)
            return _task_to_response(task, tags=saved_tags)

        @router.post("/agent-tasks/{task_id}/reconcile-events")
        async def reconcile_events_route(
            request: Request,
            task_id: str,
            body: ReconcileEventsToTaskRequest,
        ) -> dict[str, Any]:
            """Reconcile ambiguous events into pending task package items (batch)."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            specs = [
                PackageItemSpec(
                    title=item.title,
                    event_ids=item.event_ids,
                    description=item.description,
                    instructions=item.instructions,
                    internal_note=item.internal_note,
                    item_id=item.item_id,
                )
                for item in body.items or []
            ]

            def _reconcile() -> list[TaskItem | None]:
                return reconcile_events_to_task_batch(
                    task=task,
                    specs=specs,
                    task_item_store=task_item_store,
                    task_event_store=task_event_store,
                    worker_store=worker_store,
                )

            results = await asyncio.to_thread(_reconcile)
            if body.task_internal_note is not None:
                await asyncio.to_thread(
                    task_store.update,
                    task_id,
                    internal_note=body.task_internal_note,
                )
            if specs and all(result is None for result in results):
                raise OmnigentError(
                    "No claimable ambiguous events for task package item",
                    code=ErrorCode.CONFLICT,
                )
            return {
                "object": "list",
                "data": [_item_to_response(item) for item in results if item is not None],
            }

        @router.get("/agent-tasks/board/pending")
        async def list_board_pending(request: Request) -> dict[str, Any]:
            """List board FYI clusters awaiting user acknowledgment."""
            user_id = require_user(request, auth_provider)
            fyi = await asyncio.to_thread(
                list_fyi_board_cards,
                owner_user_id=_effective_user_id(user_id),
                task_item_store=task_item_store,
                task_event_store=task_event_store,
            )
            return {
                "object": "agent.task.board",
                "fyi": fyi,
            }

        @router.post("/agent-tasks/{task_id}/accept-package")
        async def accept_task_package_route(
            request: Request,
            task_id: str,
        ) -> dict[str, Any]:
            """Promote a pending package to an idle task."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            if task_role_profile_store is None:
                raise OmnigentError("Task role profile not found", code=ErrorCode.NOT_FOUND)

            def _accept() -> Task:
                return accept_task_package(
                    task=task,
                    task_store=task_store,
                    task_role_profile_store=task_role_profile_store,
                )

            accepted = await asyncio.to_thread(_accept)
            tags = await asyncio.to_thread(task_store.get_tags, task_id)
            return _task_to_response(accepted, tags=tags)

        @router.post("/agent-tasks/{task_id}/reject-package")
        async def reject_task_package_route(
            request: Request,
            task_id: str,
        ) -> dict[str, Any]:
            """Archive a pending task package and release its events."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            archived = await asyncio.to_thread(
                reject_task_package,
                task=task,
                task_store=task_store,
                task_item_store=task_item_store,
                task_event_store=task_event_store,
            )
            tags = await asyncio.to_thread(task_store.get_tags, task_id)
            return _task_to_response(archived, tags=tags)

        @router.post("/task-events/fyi-clusters")
        async def create_fyi_cluster_route(
            request: Request,
            body: CreateFyiClusterRequest,
        ) -> dict[str, Any]:
            """Create or extend a broker FYI cluster over ambiguous events."""
            user_id = require_user(request, auth_provider)

            def _create() -> FyiCluster | None:
                return create_fyi_cluster(
                    owner_user_id=_effective_user_id(user_id),
                    headline=body.headline,
                    event_ids=body.event_ids,
                    cluster_id=body.cluster_id,
                    task_item_store=task_item_store,
                    task_event_store=task_event_store,
                    rationale=body.rationale,
                )

            created = await asyncio.to_thread(_create)
            if created is None:
                raise OmnigentError(
                    "No claimable ambiguous events for FYI cluster",
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
                cluster.owner_user_id != _effective_user_id(user_id) and not _is_admin(user_id)
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
                worker_store=worker_store,
                routing_title=body.routing_title,
                routing_instructions=body.routing_instructions,
                suggested_task_id=body.suggested_task_id,
                proposed_task_title=body.proposed_task_title,
                proposed_task_internal_note=body.proposed_task_internal_note,
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
                worker_store=worker_store,
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
            task = await _get_task_or_404(body.task_id, user_id)
            profile = await _manager_role_profile_for_task(task, user_id)
            params = resolve_bootstrap_params(
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                model=body.model,
                role_profile=profile,
            )
            proposal = await asyncio.to_thread(
                find_open_adoption_proposal,
                task_event_store,
                session_id,
            )
            proposal_event, adopted_event = await adopt_session(
                session_id=session_id,
                task_id=body.task_id,
                task_store=task_store,
                task_event_store=task_event_store,
                worker_store=worker_store,
                conversation_store=conversation_store,
                params=params,
                proposal_event=proposal,
                session_creator=session_creator,
                app_state=request.app.state,
                user_id=user_id,
            )
            worker = await asyncio.to_thread(worker_store.get_by_session_id, session_id)
            return {
                "object": "agent.task.session_adoption",
                "session_id": session_id,
                "task_id": body.task_id,
                "worker_kind": worker.kind if worker is not None else None,
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

        # ── External session adoption (watcher-discovered) ──────────

        class ProposeExternalAdoptionRequest(BaseModel):
            """Request body for ``POST /v1/agent-tasks/external-sessions/propose-adoption``."""

            session_hint: str
            task_id: str | None = None
            transcript_snippet: str | None = None

        class AdoptExternalSessionRequest(BaseModel):
            """Request body for ``POST /v1/agent-tasks/external-sessions/{session_hint}/adopt``."""

            task_id: str
            host_id: str | None = None
            workspace: str | None = None
            harness: str | None = None
            model: str | None = None

        @router.post("/agent-tasks/external-sessions/propose-adoption")
        async def propose_external_adoption_route(
            request: Request,
            body: ProposeExternalAdoptionRequest,
        ) -> dict[str, Any]:
            """Create a user-gated adoption proposal for a watcher-discovered session."""
            user_id = require_user(request, auth_provider)
            task, created = await asyncio.to_thread(
                propose_external_session_adoption,
                session_hint=body.session_hint,
                task_id=body.task_id,
                task_store=task_store,
                task_event_store=task_event_store,
                owner_user_id=_effective_user_id(user_id),
                transcript_snippet=body.transcript_snippet,
            )
            return {
                "object": "agent.task.external_session_adoption_proposal",
                "task_id": task.id,
                "session_hint": body.session_hint,
                "event": _event_to_response(created),
            }

        @router.post("/agent-tasks/external-sessions/{session_hint}/adopt")
        async def adopt_external_session_route(
            request: Request,
            session_hint: str,
            body: AdoptExternalSessionRequest,
        ) -> dict[str, Any]:
            """Bind a watcher-discovered external session to a task."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(body.task_id, user_id)
            profile = await _manager_role_profile_for_task(task, user_id)
            params = resolve_bootstrap_params(
                host_id=body.host_id,
                workspace=body.workspace,
                harness=body.harness,
                model=body.model,
                role_profile=profile,
            )
            proposal = await asyncio.to_thread(
                find_open_external_adoption_proposal,
                task_event_store,
                session_hint,
            )
            proposal_event, adopted_event = await adopt_external_session(
                session_hint=session_hint,
                task_id=body.task_id,
                task_store=task_store,
                task_event_store=task_event_store,
                worker_store=worker_store,
                conversation_store=conversation_store,
                params=params,
                proposal_event=proposal,
                session_creator=session_creator,
                app_state=request.app.state,
                user_id=user_id,
            )
            worker = await asyncio.to_thread(
                worker_store.get_by_external_hint, session_hint
            )
            return {
                "object": "agent.task.external_session_adoption",
                "session_hint": session_hint,
                "task_id": body.task_id,
                "worker_id": worker.id if worker is not None else None,
                "proposal": (
                    _event_to_response(proposal_event)
                    if proposal_event.event_type == SESSION_ADOPTION_PROPOSAL
                    else None
                ),
                "event": _event_to_response(adopted_event),
            }

        @router.post("/agent-tasks/external-sessions/{session_hint}/reject-adoption")
        async def reject_external_adoption_route(
            request: Request,
            session_hint: str,
        ) -> dict[str, Any]:
            """Dismiss an external session adoption proposal."""
            user_id = require_user(request, auth_provider)
            proposal = await asyncio.to_thread(
                find_open_external_adoption_proposal,
                task_event_store,
                session_hint,
            )
            dismissed = await asyncio.to_thread(
                reject_external_session_adoption,
                session_hint=session_hint,
                task_event_store=task_event_store,
                proposal_event=proposal,
            )
            return {
                "object": "agent.task.external_session_adoption_rejection",
                "session_hint": session_hint,
                "proposal": _event_to_response(dismissed) if dismissed is not None else None,
            }

    return router
