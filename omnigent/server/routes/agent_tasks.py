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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omnigent.agent_tasks.adoption import (
    SESSION_ADOPTED,
    adopt_external_session,
    adopt_session_to_task,
    find_open_external_adoption_proposal,
    propose_external_session_adoption,
    reject_external_session_adoption,
)
from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    TASK_SECRETARY_ROLE,
)
from omnigent.agent_tasks.bootstrap import bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.bootstrap import ensure_puppygarden_project
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
from omnigent.agent_tasks.ingress import ingress_event
from omnigent.agent_tasks.internal_worker import initialize_internal_worker
from omnigent.agent_tasks.items import (
    complete_human_action,
    create_task_item,
    item_dispatch_payload,
    patch_task_item,
    reconcile_events,
    reject_task_item,
    resolve_task_item,
    submit_item_for_user_ack,
)
from omnigent.agent_tasks.manager_discovery import (
    list_active_managers,
    manager_role_profile_response,
)
from omnigent.agent_tasks.manager_role_profile import (
    get_or_create_manager_role_profile,
)
from omnigent.agent_tasks.role_keys import (
    MANAGER_ROLE_PREFIX,
    SYSTEM_ROLE_KEYS,
    is_deletable_role_key,
    is_manager_role_key,
    is_system_role_key,
    manager_role_key_from_slug,
    normalize_role_profile_key,
    role_kind_from_key,
    role_profile_title,
)
from omnigent.agent_tasks.task_match import (
    _LIVE_TASK_STATES,
    collect_event_tags,
    load_events,
    rank_tasks_for_events,
    ranked_task_payload,
    routable_tasks,
    task_tags_from_event_tags,
)
from omnigent.agent_tasks.task_search import (
    SEARCH_MATCH_LIMIT,
    SEARCH_RECENT_LIMIT,
    rank_tasks_by_text,
)
from omnigent.agent_tasks.task_packages import (
    PackageItemSpec,
    accept_task_package,
    create_task_package,
    reconcile_events_to_task_batch,
    reject_task_package,
)
from omnigent.agent_tasks.workers import worker_for_item
from omnigent.db.enum_codecs import TASK_STATE
from omnigent.db.utils import now_epoch
from omnigent.entities import (
    FyiCluster,
    Task,
    TaskAsset,
    TaskEventExecution,
    TaskEventSubscription,
    TaskItem,
    TaskTag,
    Worker,
)
from omnigent.entities.agent_queue import AgentQueueKey
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runner.routing import RunnerRouter
from omnigent.server.auth import LEVEL_OWNER, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access, require_user
from omnigent.server.routes.task_events import _event_to_response
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.prompt_profile_store import PromptProfileStore
from omnigent.stores.task_asset_store import TaskAssetStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.user_role_session_store import UserRoleSessionStore
from omnigent.stores.worker_provider_store import WorkerProviderStore
from omnigent.stores.worker_store import WORKER_KIND_EXTERNAL, WorkerStore

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


class AdoptSessionRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/sessions/{session_id}/adopt``."""

    task_id: str


class CreateAgentTaskRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks``."""

    model_config = ConfigDict(extra="forbid")

    title: str
    goal: str
    description: str | None = None
    internal_note: str | None = None
    state: str = "active"
    priority: int = Field(default=2, ge=0, le=3)
    tags: list[TaskTagInput] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped

    @field_validator("goal")
    @classmethod
    def _goal_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("goal must be a non-empty string")
        return stripped

    @field_validator("state")
    @classmethod
    def _validate_state(cls, value: str) -> str:
        allowed = {"active", "pending"}
        if value not in allowed:
            raise ValueError(f"state must be one of: {', '.join(sorted(allowed))}")
        return value


class BatchGetTasksRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/batch``."""

    task_ids: list[str] = Field(min_length=1, max_length=100)


class UpdateAgentTaskRequest(BaseModel):
    """Request body for ``PATCH /v1/agent-tasks/{task_id}``."""

    title: str | None = None
    description: str | None = None
    internal_note: str | None = None
    goal: str | None = None
    manager_role_key: str | None = None
    manager_conversation_id: str | None = None
    state: str | None = None
    priority: int | None = Field(default=None, ge=0, le=3)

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped

    @field_validator("goal")
    @classmethod
    def _goal_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("goal must be a non-empty string")
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
    """Editable metadata for a PuppyGarden role manual."""

    name: str | None = None
    harness: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    description: str | None = None

    @property
    def clears_model(self) -> bool:
        """Whether the caller explicitly sent ``model: null`` to clear it."""
        return "model" in self.model_fields_set and self.model is None


class PutAgentRoleProfileRequest(_RoleProfileFieldsMixin):
    """Request body for ``PUT /v1/agent-tasks/roles/{role}/profile``.

    ``agent_profile_id`` is optional: omitting it preserves the role's
    current binding (the prompt/import endpoints manage that binding now).
    """

    agent_profile_id: str | None = None


class UpdateRolePromptRequest(BaseModel):
    """Request body for ``PUT /v1/agent-tasks/roles/{role}/prompt``.

    Sets the role's prompt by editing its bound backing profile's bundle in
    place. If the role is still bound to a shared packaged agent, it is
    auto-forked first (preserving the packaged spec) so the edit never
    mutates a shared built-in.
    """

    prompt: str


class CreateManagerRoleProfileRequest(_RoleProfileFieldsMixin):
    """Request body for ``POST /v1/agent-tasks/roles/manager``."""

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
    worker_id: str | None = None
    state: str = "draft"
    kind: Literal["work", "human_action"] = "work"
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
    category: Literal["code", "tests", "documents", "logs", "other"] = "other"
    title: str
    url: str

    @field_validator("title", "url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be a non-empty string")
        return stripped


class ReassignWorkerRequest(BaseModel):
    """Request body for ``POST /v1/task-workers/{worker_id}/reassign``."""

    task_id: str


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

    instructions: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    model: str | None = None


class ResolveTaskItemRequest(BaseModel):
    """Request body for ``POST /v1/task-items/{item_id}/resolve``."""

    resolution: Literal["accept_item", "edit_and_dispatch", "reject_item", "mark_done"]
    edited_payload: dict[str, Any] | None = None


class CreateWorkerRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/workers``."""

    provider_id: str = Field(min_length=1)
    host_id: str = Field(min_length=1)
    workspace: str = Field(min_length=1)


class CreateEventSubscriptionRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/event-subscriptions``."""

    source: str = Field(min_length=1)
    source_key: str = Field(min_length=1)


class QueueHoldRequest(BaseModel):
    """Optional token used to renew an existing temporary queue hold."""

    token: str | None = None


class WorkerAssignmentInput(BaseModel):
    """One item→lane assignment for batch worker assignment."""

    item_id: str
    provider_id: str | None = None
    worker_id: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    edit_lease_token: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> WorkerAssignmentInput:
        if self.provider_id is None and self.worker_id is None:
            raise ValueError("provide either provider_id or worker_id")
        if self.provider_id is not None and (
            not self.host_id
            or not self.host_id.strip()
            or not self.workspace
            or not self.workspace.strip()
        ):
            raise ValueError("host_id and workspace are required with provider_id")
        return self


class BatchAssignWorkersRequest(BaseModel):
    """Request body for ``POST /v1/agent-tasks/{task_id}/workers/assign``."""

    assignments: list[WorkerAssignmentInput] = Field(min_length=1)


class UpdateTaskItemRequest(BaseModel):
    """Request body for ``PATCH /v1/task-items/{item_id}``."""

    title: str | None = None
    description: str | None = None
    instructions: str | None = None
    internal_note: str | None = None
    worker_id: str | None = None
    edit_lease_token: str | None = None

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


class RerouteEventRequest(BaseModel):
    """Request body for ``POST /v1/task-events/{event_id}/reroute``."""

    task_id: str = Field(min_length=1)


class PackageItemInput(BaseModel):
    """One backlog item on a pending task package."""

    title: str
    event_ids: list[str] = Field(min_length=1)
    description: str | None = None
    instructions: str | None = None
    internal_note: str | None = None
    item_id: str | None = None
    worker_id: str | None = None

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
    goal: str
    description: str | None = None
    internal_note: str | None = None
    tags: list[TaskTagInput] = Field(default_factory=list)
    items: list[PackageItemInput] = Field(min_length=1)
    # Manager session to attach the task to at birth. Omitted by the user;
    # managers pass their own session id so the task is born attached.
    manager_conversation_id: str | None = None

    @field_validator("title", "goal")
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
    proposed_task_goal: str | None = None
    proposed_task_internal_note: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None


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
    try:
        snapshot = json.loads(worker.provider_configuration or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    launch = snapshot.get("launch") if isinstance(snapshot, dict) else None
    if not isinstance(launch, dict):
        launch = {}
    return {
        "object": "agent.task.worker",
        "id": worker.id,
        "worker_id": worker.id,
        "task_id": worker.task_id,
        "kind": worker.kind,
        "target_id": worker.target_id,
        "state": worker.state,
        "needs_response": worker.needs_response,
        "provider_name": worker.provider_name,
        "host_id": launch.get("host_id"),
        "workspace": launch.get("workspace"),
        "failure_reason": worker.failure_reason,
        "last_observed_at": worker.last_observed_at,
    }


def _task_to_response(task: Task, *, tags: list[TaskTag] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": task.id,
        "object": "agent.task",
        "manager_role_key": task.manager_role_key,
        "manager_conversation_id": task.manager_conversation_id,
        "owner_user_id": task.owner_user_id,
        "title": task.title,
        "description": task.description,
        "internal_note": task.internal_note,
        "goal": task.goal,
        "state": task.state,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "priority": task.priority,
        "queue_rank": task.queue_rank,
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
    parent_session_id: str | None = None,
    sub_agent_name: str | None = None,
    title: str | None = None,
    project_id: str | None = None,
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
        project_id=project_id,
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
    agent_name: str | None = None,
    candidate_agents: list[dict[str, str]] | None = None,
    prompt: str | None = None,
    profile_name: str | None = None,
    profile_description: str | None = None,
) -> dict[str, Any]:
    return {
        "object": "agent.task.role_profile",
        "role": role,
        "title": profile_name or role_profile_title(role),
        "kind": profile.kind,
        "system": is_system_role_key(role),
        "deletable": is_deletable_role_key(role),
        "agent_profile_id": profile.agent_profile_id,
        "agent_name": agent_name,
        "candidate_agents": candidate_agents or [],
        "prompt": prompt,
        "conversation_id": conversation_id,
        "harness": profile.harness,
        "model": profile.model,
        "host_id": profile.host_id,
        "workspace": profile.workspace,
        "description": profile_description
        if profile_description is not None
        else profile.description,
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


def _subscription_to_response(subscription: TaskEventSubscription) -> dict[str, Any]:
    return {
        "id": subscription.id,
        "object": "agent.task.event_subscription",
        "task_id": subscription.task_id,
        "source": subscription.source,
        "source_key": subscription.source_key,
        "owner_user_id": subscription.owner_user_id,
        "created_at": subscription.created_at,
    }


def _execution_to_response(execution: TaskEventExecution) -> dict[str, Any]:
    return {
        "id": execution.id,
        "object": "agent.task.execution",
        "task_item_id": execution.task_item_id,
        "task_id": execution.task_id,
        "agent_queue_item_id": execution.agent_queue_item_id,
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
        "category": asset.category,
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
        "kind": item.kind,
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
    artifact_store: Any | None = None,
    agent_cache: Any | None = None,
    prompt_profile_store: PromptProfileStore | None = None,
    worker_provider_store: WorkerProviderStore | None = None,
    runner_router: RunnerRouter | None = None,
    project_store: Any = None,
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
    :param prompt_profile_store: Stores the hidden manuals bound to PuppyGarden roles.
    :param auth_provider: Auth provider for owner attribution and access
        checks. ``None`` disables auth enforcement.
    :param permission_store: Used to let admins list/view any task.
        ``None`` disables admin bypass.
    :returns: A configured :class:`APIRouter`.
    """
    _ = (artifact_store, agent_cache)
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

    async def _resolve_role_agent_fields(
        profile: TaskRoleProfile,
        *,
        include_prompt: bool = False,
    ) -> tuple[str | None, list[dict[str, Any]], str | None]:
        """Resolve the fixed OmniHarness target and the role's manual."""
        bound_agent = (
            await asyncio.to_thread(agent_store.get, profile.agent_profile_id)
            if profile.agent_profile_id
            else None
        )
        prompt: str | None = None
        if include_prompt and profile.prompt_profile_id and prompt_profile_store is not None:
            manual = await asyncio.to_thread(prompt_profile_store.get, profile.prompt_profile_id)
            prompt = manual.instructions if manual is not None else None
        return bound_agent.name if bound_agent else None, [], prompt

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
        """Create a managed task.

        ``state="active"`` bootstraps the manager session inline (spins up the
        manager); ``state="pending"`` leaves the task as a broker-managed
        suggestion.
        """
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
            goal=body.goal,
            state=body.state,
            priority=body.priority,
            tags=tags,
        )
        if body.state == "active":
            task = await _bootstrap_manager_for_task(request, task, user_id)
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

    @router.post("/agent-tasks/batch")
    async def batch_get_tasks(
        request: Request,
        body: BatchGetTasksRequest,
    ) -> dict[str, Any]:
        """Fetch multiple tasks by ID in one call."""
        user_id = get_user_id(request, auth_provider)

        def _fetch() -> list[Task]:
            tasks: list[Task] = []
            for task_id in body.task_ids:
                task = task_store.get(task_id.strip())
                if task is None:
                    continue
                tasks.append(task)
            return _filter_tasks_for_user(tasks, user_id)

        tasks = await asyncio.to_thread(_fetch)
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
            prompt_profile_store=prompt_profile_store,
        )

    async def _bootstrap_manager_for_task(
        request: Request,
        task: Task,
        user_id: str | None,
    ) -> Task:
        """Spin up the manager session for ``task`` (used by create-on-active)."""
        if conversation_store is None or session_creator is None:
            raise OmnigentError(
                "manager bootstrap is not configured on this server",
                code=ErrorCode.INVALID_INPUT,
            )
        profile = await _manager_role_profile_for_task(task, user_id)
        params = resolve_bootstrap_params(
            host_id=None,
            workspace=None,
            harness=None,
            model=None,
            role_profile=profile,
        )
        return await bootstrap_task_manager(
            task=task,
            task_store=task_store,
            conversation_store=conversation_store,
            params=params,
            session_creator=session_creator,
            app_state=request.app.state,
            user_id=user_id,
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
                prompt_profile_store=prompt_profile_store,
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

    async def _role_profile_name(profile: TaskRoleProfile) -> str | None:
        if profile.prompt_profile_id is None or prompt_profile_store is None:
            return None
        manual = await asyncio.to_thread(prompt_profile_store.get, profile.prompt_profile_id)
        return manual.name if manual is not None else None

    async def _role_profile_description(profile: TaskRoleProfile) -> str | None:
        if profile.prompt_profile_id is None or prompt_profile_store is None:
            return None
        manual = await asyncio.to_thread(prompt_profile_store.get, profile.prompt_profile_id)
        return manual.description if manual is not None else None

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
            prompt_profile_store=prompt_profile_store,
        )

    if task_role_profile_store is not None:

        @router.get("/agent-tasks/roles/profiles")
        async def list_agent_role_profiles(
            request: Request,
            prefix: str | None = Query(default=None),
            kind: str | None = Query(default=None),
        ) -> dict[str, Any]:
            """List the caller's glossary role profiles.

            Optional filters select a manager prefix or one of the broker,
            secretary, and manager role families.
            """
            user_id = require_user(request, auth_provider)
            await _ensure_system_role_profiles(user_id)
            profiles = await asyncio.to_thread(task_role_profile_store.list_roles)
            if prefix is not None:
                profiles = [p for p in profiles if p.role.startswith(prefix)]
            if kind is not None:
                profiles = [p for p in profiles if role_kind_from_key(p.role) == kind]
            items = []
            for profile in profiles:
                agent_name, candidates, _prompt = await _resolve_role_agent_fields(profile)
                items.append(
                    _agent_role_profile_to_response(
                        profile.role,
                        profile,
                        conversation_id=await _role_conversation_id(profile.role, user_id),
                        agent_name=agent_name,
                        candidate_agents=candidates,
                        profile_name=await _role_profile_name(profile),
                        profile_description=await _role_profile_description(profile),
                    )
                )
            return {
                "object": "list",
                "data": items,
            }

        @router.post("/agent-tasks/roles/manager")
        async def create_manager_role_profile(
            request: Request,
            body: CreateManagerRoleProfileRequest,
        ) -> dict[str, Any]:
            """Create a custom OmniHarness manager backed by a hidden PromptProfile."""
            user_id = require_user(request, auth_provider)
            role = manager_role_key_from_slug(body.slug)
            if await asyncio.to_thread(task_role_profile_store.get, role) is not None:
                raise OmnigentError(
                    f"Manager role already exists: {role}",
                    code=ErrorCode.CONFLICT,
                )
            profile = await asyncio.to_thread(
                ensure_role_profile,
                role=role,
                auth_user_id=user_id,
                task_role_profile_store=task_role_profile_store,
                agent_store=agent_store,
                host_store=host_store,
                prompt_profile_store=prompt_profile_store,
            )
            if body.description is not None and profile.prompt_profile_id and prompt_profile_store:
                await asyncio.to_thread(
                    prompt_profile_store.update,
                    profile.prompt_profile_id,
                    description=body.description,
                )
            agent_name, _candidates, prompt = await _resolve_role_agent_fields(
                profile, include_prompt=True
            )
            return _agent_role_profile_to_response(
                role,
                profile,
                agent_name=agent_name,
                candidate_agents=[],
                prompt=prompt,
                profile_name=await _role_profile_name(profile),
                profile_description=await _role_profile_description(profile),
            )

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
                )
                conflict_message = "Manager role is assigned to tasks"
            else:
                in_use = 0
                conflict_message = "Role is assigned to pending tasks"
            if in_use > 0:
                raise OmnigentError(
                    conflict_message,
                    code=ErrorCode.CONFLICT,
                )
            profile = await asyncio.to_thread(task_role_profile_store.get, role)
            deleted = await asyncio.to_thread(task_role_profile_store.delete, role)
            if deleted and profile and profile.prompt_profile_id and prompt_profile_store:
                await asyncio.to_thread(prompt_profile_store.delete, profile.prompt_profile_id)
            if not deleted:
                raise OmnigentError("Task role profile not found", code=ErrorCode.NOT_FOUND)
            return {"object": "agent.task.role_profile", "role": role, "deleted": True}

        @router.get("/agent-tasks/roles/{role}/profile")
        async def get_agent_role_profile(request: Request, role: str) -> dict[str, Any]:
            """Return the caller's profile for a managed task agent role."""
            role = _require_task_agent_role(role)
            user_id = require_user(request, auth_provider)
            profile = await _load_role_profile(role, user_id)
            agent_name, candidates, prompt = await _resolve_role_agent_fields(
                profile, include_prompt=True
            )
            return _agent_role_profile_to_response(
                role,
                profile,
                conversation_id=await _role_conversation_id(role, user_id),
                agent_name=agent_name,
                candidate_agents=candidates,
                prompt=prompt,
                profile_name=await _role_profile_name(profile),
                profile_description=await _role_profile_description(profile),
            )

        @router.put("/agent-tasks/roles/{role}/profile")
        async def put_agent_role_profile(
            request: Request,
            role: str,
            body: PutAgentRoleProfileRequest,
        ) -> dict[str, Any]:
            """Update the hidden PromptProfile and launch settings for a role."""
            role = _require_task_agent_role(role)
            user_id = require_user(request, auth_provider)
            profile = await _load_role_profile(role, user_id)
            if profile.prompt_profile_id is None or prompt_profile_store is None:
                raise OmnigentError("Role manual is unavailable", code=ErrorCode.INTERNAL_ERROR)
            fields: dict[str, Any] = {}
            if "name" in body.model_fields_set and body.name is not None:
                fields["name"] = body.name.strip()
            if "description" in body.model_fields_set:
                fields["description"] = body.description
            if fields:
                await asyncio.to_thread(
                    prompt_profile_store.update, profile.prompt_profile_id, **fields
                )
            if "model" in body.model_fields_set:
                profile = await asyncio.to_thread(
                    task_role_profile_store.upsert,
                    role,
                    model=body.model,
                    clear_model=body.clears_model,
                )
            agent_name, _candidates, prompt = await _resolve_role_agent_fields(
                profile, include_prompt=True
            )
            return _agent_role_profile_to_response(
                role,
                profile,
                conversation_id=await _role_conversation_id(role, user_id),
                agent_name=agent_name,
                candidate_agents=[],
                prompt=prompt,
                profile_name=await _role_profile_name(profile),
                profile_description=await _role_profile_description(profile),
            )

        @router.put("/agent-tasks/roles/{role}/prompt")
        async def update_role_prompt(
            request: Request,
            role: str,
            body: UpdateRolePromptRequest,
        ) -> dict[str, Any]:
            """Update the role manual without mutating an agent bundle."""
            role = _require_task_agent_role(role)
            user_id = require_user(request, auth_provider)
            profile = await _load_role_profile(role, user_id)
            if profile.prompt_profile_id is None or prompt_profile_store is None:
                raise OmnigentError("Role manual is unavailable", code=ErrorCode.INTERNAL_ERROR)
            await asyncio.to_thread(
                prompt_profile_store.update,
                profile.prompt_profile_id,
                instructions=body.prompt,
            )
            agent_name, _candidates, prompt = await _resolve_role_agent_fields(
                profile, include_prompt=True
            )
            return _agent_role_profile_to_response(
                role,
                profile,
                conversation_id=await _role_conversation_id(role, user_id),
                agent_name=agent_name,
                candidate_agents=[],
                prompt=prompt,
                profile_name=await _role_profile_name(profile),
                profile_description=await _role_profile_description(profile),
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
                    project_id=await asyncio.to_thread(
                        ensure_puppygarden_project, project_store, user_id
                    ),
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
                    project_id=await asyncio.to_thread(
                        ensure_puppygarden_project, project_store, user_id
                    ),
                )
                await _bind_role_session(effective_user_id, role, conversation_id)
                # Orphan sessions are now durable ``session.orphan`` events the
                # broker packager polls, so there is nothing to flush here.
                return _agent_role_session_to_response(
                    role,
                    conversation_id=conversation_id,
                    created=True,
                )

    @router.get("/agent-tasks/managers")
    async def list_managers(request: Request) -> dict[str, Any]:
        """List the caller's active managers with task portfolios and capacity.

        The broker distributor picks a manager from this list (or spins up a
        new one from the returned role profiles when none fits).
        """
        user_id = require_user(request, auth_provider)
        owner = _effective_user_id(user_id)
        if conversation_store is None:
            return {"object": "list", "managers": [], "role_profiles": []}
        managers = await asyncio.to_thread(
            list_active_managers,
            owner_user_id=owner,
            task_store=task_store,
            conversation_store=conversation_store,
        )
        role_profiles: list = []
        if task_role_profile_store is not None:
            profiles = await asyncio.to_thread(
                task_role_profile_store.list_roles, kind="manager"
            )
            role_profiles = manager_role_profile_response(profiles)
        return {
            "object": "list",
            "managers": [
                {
                    "conversation_id": manager.conversation_id,
                    "title": manager.title,
                    "host_id": manager.host_id,
                    "workspace": manager.workspace,
                    "task_count": manager.task_count,
                    "tasks": [
                        {
                            "task_id": task.id,
                            "title": task.title,
                            "state": task.state,
                        }
                        for task in manager.tasks
                    ],
                }
                for manager in managers
            ],
            "role_profiles": role_profiles,
        }

    @router.get("/agent-tasks/search")
    async def search_agent_tasks(
        request: Request,
        q: str = "",
        session_id: str | None = None,
        event_id: str | None = None,
        limit: int = SEARCH_MATCH_LIMIT,
    ) -> dict[str, Any]:
        """Three-list task search for managers: recent + text matches + tag matches.

        - ``recent``: the owner's most recently touched tasks (no state filter).
          With ``session_id``, tasks bound to that session come first.
        - ``matches``: fuzzy text match over title/goal/description/internal_note.
        - ``tag_matches``: tag-overlap ranking, computed when ``event_id`` is given.
        """
        user_id = get_user_id(request, auth_provider)
        limit = max(1, min(limit, SEARCH_MATCH_LIMIT))

        recent = await asyncio.to_thread(task_store.list_recent, SEARCH_RECENT_LIMIT)
        recent = _filter_tasks_for_user(recent, user_id)
        if session_id:
            # Session-first ordering only applies to worker-bound sessions;
            # manager/plain sessions have no worker row to look up.
            bound = (
                await asyncio.to_thread(worker_store.get_by_target_id, session_id)
                if worker_store is not None
                else None
            )
            if bound is not None:
                bound_task = await asyncio.to_thread(task_store.get, bound.task_id)
                bound_task = (
                    bound_task
                    if bound_task is not None and _filter_tasks_for_user([bound_task], user_id)
                    else None
                )
                if bound_task is not None:
                    recent = [bound_task] + [t for t in recent if t.id != bound_task.id]
        recent = recent[:SEARCH_RECENT_LIMIT]

        candidates = _filter_tasks_for_user(
            await asyncio.to_thread(task_store.list), user_id
        )
        matches = rank_tasks_by_text(candidates, q, limit=limit) if q.strip() else []
        tag_matches: list = []
        if event_id:
            events = await asyncio.to_thread(load_events, [event_id], task_event_store=task_event_store)
            tag_matches = await asyncio.to_thread(
                rank_tasks_for_events,
                events=events,
                tasks=candidates,
                task_store=task_store,
                limit=limit,
            )
        return {
            "object": "task.search",
            "recent": [
                {
                    "task_id": task.id,
                    "title": task.title,
                    "state": task.state,
                    "updated_at": task.updated_at or task.created_at,
                }
                for task in recent
            ],
            "matches": ranked_task_payload(matches),
            "tag_matches": ranked_task_payload(tag_matches),
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
        task = await _get_task_or_404(task_id, user_id)
        update_kwargs: dict[str, Any] = {}
        for field in (
            "title",
            "description",
            "internal_note",
            "goal",
            "manager_conversation_id",
            "state",
            "priority",
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

    @router.post("/agent-tasks/{task_id}/move-to-queue-end")
    async def move_task_to_queue_end(request: Request, task_id: str) -> dict[str, Any]:
        """Move one task to the end of the stable board ordering."""
        user_id = require_user(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        task = await asyncio.to_thread(task_store.move_to_queue_end, task_id)
        assert task is not None
        return _task_to_response(task)

    @router.post("/agent-tasks/{task_id}/manager-queue-hold")
    async def hold_manager_queue(
        request: Request, task_id: str, body: QueueHoldRequest
    ) -> dict[str, Any]:
        """Temporarily stop new manager dispatches while the user inspects chat."""
        user_id = require_user(request, auth_provider)
        task = await _get_task_or_404(task_id, user_id)
        if agent_queue_store is None:
            raise OmnigentError("Agent queue is unavailable", code=ErrorCode.INTERNAL_ERROR)
        if task.manager_conversation_id is None:
            raise OmnigentError("Task has no manager queue", code=ErrorCode.CONFLICT)
        token = body.token or uuid.uuid4().hex
        now = now_epoch()
        key = AgentQueueKey(
            role="manager",
            owner_user_id=_effective_user_id(user_id),
            scope_id=task.manager_conversation_id,
        )
        try:
            queue = await asyncio.to_thread(
                agent_queue_store.acquire_inspection_hold,
                key,
                token,
                now=now,
                ttl_s=90,
            )
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.CONFLICT) from exc
        return {
            "object": "agent.queue.hold",
            "token": token,
            "expires_at": queue.inspection_hold_expires_at,
        }

    @router.delete("/agent-tasks/{task_id}/manager-queue-hold/{token}")
    async def release_manager_queue_hold(
        request: Request, task_id: str, token: str
    ) -> dict[str, Any]:
        """Release the caller's temporary manager dispatch hold."""
        user_id = require_user(request, auth_provider)
        task = await _get_task_or_404(task_id, user_id)
        if agent_queue_store is None:
            raise OmnigentError("Agent queue is unavailable", code=ErrorCode.INTERNAL_ERROR)
        if task.manager_conversation_id is None:
            raise OmnigentError("Task has no manager queue", code=ErrorCode.CONFLICT)
        key = AgentQueueKey(
            role="manager",
            owner_user_id=_effective_user_id(user_id),
            scope_id=task.manager_conversation_id,
        )
        released = await asyncio.to_thread(agent_queue_store.release_inspection_hold, key, token)
        return {"object": "agent.queue.hold.release", "released": released}

    @router.delete("/agent-tasks/{task_id}")
    async def delete_task(request: Request, task_id: str) -> dict[str, Any]:
        """Archive a managed task (soft delete)."""
        user_id = require_user(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        task = await asyncio.to_thread(task_store.update, task_id, state="archived")
        if task is None:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        return {"id": task_id, "object": "agent.task", "deleted": True, "state": task.state}

    @router.delete("/agent-tasks/{task_id}/permanent")
    async def permanently_delete_task(request: Request, task_id: str) -> dict[str, Any]:
        """Permanently delete a managed task and its related data.

        Deletes task items (except running/queued), assets, event
        subscriptions, soft-deletes worker rows, and tags. Manager and
        worker sessions are left running as regular conversations. Queued
        (not yet dispatched) agent-queue items for this task are cancelled;
        in-flight items are left to finish naturally. Events, executions,
        and routing history are preserved.
        """
        user_id = require_user(request, auth_provider)
        task = await _get_task_or_404(task_id, user_id)
        # Cancel queued agent-queue items for this task's manager queue (keyed
        # by the manager session) and its worker queues (keyed by worker id).
        # In-flight (dispatched) items are left alone — the agent is already
        # working on them and will finish naturally.
        if agent_queue_store is not None:
            from omnigent.db.utils import now_epoch
            from omnigent.entities import AgentQueueKey
            if task.manager_conversation_id is not None:
                manager_key = AgentQueueKey(
                    role="manager",
                    owner_user_id=_effective_user_id(user_id),
                    scope_id=task.manager_conversation_id,
                )
                for item in await asyncio.to_thread(
                    agent_queue_store.list_items, manager_key, state="queued"
                ):
                    await asyncio.to_thread(
                        agent_queue_store.cancel_item, item.id, now=now_epoch()
                    )
            for worker in await asyncio.to_thread(worker_store.list_workers_for_task, task_id):
                worker_key = AgentQueueKey(
                    role="worker",
                    owner_user_id=_effective_user_id(user_id),
                    scope_id=worker.id,
                )
                for item in await asyncio.to_thread(
                    agent_queue_store.list_items, worker_key, state="queued"
                ):
                    await asyncio.to_thread(
                        agent_queue_store.cancel_item, item.id, now=now_epoch()
                    )
        # Delete non-running, non-queued items. Running/queued items
        # are still active and left in place.
        await asyncio.to_thread(
            task_item_store.delete_items_for_task,
            task_id,
            exclude_states={"running", "queued"},
        )
        # Delete all assets for this task.
        await asyncio.to_thread(task_asset_store.delete_assets_for_task, task_id)
        # Delete all event subscriptions for this task.
        await asyncio.to_thread(task_event_store.delete_subscriptions_for_task, task_id)
        # Soft-delete workers and delete tags, then the task itself.
        deleted = await asyncio.to_thread(task_store.delete, task_id)
        if not deleted:
            raise OmnigentError("Task not found", code=ErrorCode.NOT_FOUND)
        return {"id": task_id, "object": "agent.task", "deleted": True, "permanent": True}

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

    @router.post("/agent-tasks/{task_id}/event-subscriptions", status_code=201)
    async def create_event_subscription(
        request: Request,
        task_id: str,
        body: CreateEventSubscriptionRequest,
    ) -> dict[str, Any]:
        """Subscribe a task to an event ``(source, source_key)`` pair."""
        user_id = require_user(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        subscription = await asyncio.to_thread(
            task_event_store.create_subscription,
            uuid.uuid4().hex,
            task_id,
            source=body.source.strip(),
            source_key=body.source_key.strip(),
            owner_user_id=user_id,
        )
        return _subscription_to_response(subscription)

    @router.get("/agent-tasks/{task_id}/event-subscriptions")
    async def list_event_subscriptions(request: Request, task_id: str) -> dict[str, Any]:
        """List a task's event subscriptions."""
        user_id = get_user_id(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        subscriptions = await asyncio.to_thread(
            task_event_store.list_subscriptions_for_task, task_id
        )
        return {
            "object": "list",
            "data": [_subscription_to_response(sub) for sub in subscriptions],
        }

    @router.delete("/agent-tasks/{task_id}/event-subscriptions/{subscription_id}")
    async def delete_event_subscription(
        request: Request,
        task_id: str,
        subscription_id: str,
    ) -> dict[str, Any]:
        """Remove one of a task's event subscriptions."""
        user_id = require_user(request, auth_provider)
        await _get_task_or_404(task_id, user_id)
        subscription = await asyncio.to_thread(task_event_store.get_subscription, subscription_id)
        if subscription is None or subscription.task_id != task_id:
            raise OmnigentError("Event subscription not found", code=ErrorCode.NOT_FOUND)
        await asyncio.to_thread(task_event_store.delete_subscription, subscription_id)
        return {"id": subscription_id, "object": "agent.task.event_subscription", "deleted": True}

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

        @router.get("/agent-tasks/{task_id}/workers")
        async def list_task_workers(
            request: Request,
            task_id: str,
        ) -> dict[str, Any]:
            """List the worker lanes on a task."""
            user_id = get_user_id(request, auth_provider)
            await _get_task_or_404(task_id, user_id)
            workers = await asyncio.to_thread(worker_store.list_workers_for_task, task_id)
            return {
                "object": "list",
                "data": [_worker_to_response(w) for w in workers],
            }

        def _create_worker_from_provider(
            task_id: str,
            provider_id: str,
            *,
            host_id: str,
            workspace: str,
        ) -> Worker:
            if worker_provider_store is None:
                raise OmnigentError(
                    "Worker providers are unavailable", code=ErrorCode.INTERNAL_ERROR
                )
            provider = worker_provider_store.get(provider_id)
            if provider is None:
                raise OmnigentError("Worker provider not found", code=ErrorCode.NOT_FOUND)
            configuration = json.loads(provider.configuration)
            if provider.kind == "internal":
                configuration = {
                    key: configuration.get(key)
                    for key in ("agent_id", "model")
                    if configuration.get(key) is not None
                }
            snapshot = json.dumps(
                {
                    "version": 2,
                    "provider_id": provider.id,
                    "kind": provider.kind,
                    "configuration": configuration,
                    "launch": {"host_id": host_id.strip(), "workspace": workspace.strip()},
                },
                sort_keys=True,
            )
            return worker_store.create_worker(
                uuid.uuid4().hex,
                task_id,
                kind="managed",
                provider_name=provider.name,
                provider_configuration=snapshot,
                state="uninitialized",
            )

        @router.post("/agent-tasks/{task_id}/workers")
        async def create_worker(
            request: Request,
            task_id: str,
            body: CreateWorkerRequest,
        ) -> dict[str, Any]:
            """Create one durable, uninitialized Worker from a provider snapshot."""
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            if task.state == "pending":
                raise OmnigentError(
                    "Cannot create workers on a pending task", code=ErrorCode.CONFLICT
                )
            worker = await asyncio.to_thread(
                _create_worker_from_provider,
                task_id,
                body.provider_id,
                host_id=body.host_id,
                workspace=body.workspace,
            )
            return _worker_to_response(worker)

        @router.post("/agent-tasks/{task_id}/workers/assign")
        async def batch_assign_workers(
            request: Request, task_id: str, body: BatchAssignWorkersRequest
        ) -> dict[str, Any]:
            user_id = require_user(request, auth_provider)
            task = await _get_task_or_404(task_id, user_id)
            if task.state == "pending":
                raise OmnigentError(
                    "Cannot assign workers on a pending task", code=ErrorCode.CONFLICT
                )

            def _assign() -> list[dict[str, Any]]:
                result = []
                for assignment in body.assignments:
                    item = task_item_store.get_item(assignment.item_id)
                    if item is None or item.task_id != task_id:
                        raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
                    if item.kind == "human_action":
                        raise OmnigentError(
                            "Human action items are completed by the user, not a worker",
                            code=ErrorCode.CONFLICT,
                        )
                    worker = (
                        worker_store.get_worker(assignment.worker_id)
                        if assignment.worker_id
                        else _create_worker_from_provider(
                            task_id,
                            assignment.provider_id or "",
                            host_id=assignment.host_id or "",
                            workspace=assignment.workspace or "",
                        )
                    )
                    if worker is None:
                        raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
                    # A worker lane may serve any task of the same owner.
                    if worker.task_id != task_id:
                        home_task = task_store.get(worker.task_id)
                        if home_task is None or home_task.owner_user_id != task.owner_user_id:
                            raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
                    if (
                        item.state in {"queued", "interrupted", "dispatch_failed"}
                        and item.worker_id != worker.id
                    ):
                        if agent_queue_store is None:
                            raise OmnigentError(
                                "Agent queue is unavailable", code=ErrorCode.INTERNAL_ERROR
                            )
                        queue_item = agent_queue_store.find_open_item_for_source(
                            item.id, role="worker"
                        )
                        now = now_epoch()
                        if item.state == "queued" and (
                            queue_item is None
                            or not assignment.edit_lease_token
                            or queue_item.edit_lease_token != assignment.edit_lease_token
                            or queue_item.edit_lease_expires_at is None
                            or queue_item.edit_lease_expires_at <= now
                        ):
                            raise OmnigentError(
                                "An active edit lease is required to reassign queued work",
                                code=ErrorCode.CONFLICT,
                            )
                        if (
                            queue_item is not None
                            and agent_queue_store.cancel_item(queue_item.id, now=now) is None
                        ):
                            raise OmnigentError(
                                "Queued delivery could not be moved", code=ErrorCode.CONFLICT
                            )
                        if item.state == "queued":
                            agent_queue_store.enqueue(
                                uuid.uuid4().hex,
                                AgentQueueKey(
                                    role="worker",
                                    owner_user_id=_effective_user_id(user_id),
                                    scope_id=worker.id,
                                ),
                                kind="item.dispatch",
                                source_ids=[item.id],
                                payload=json.dumps(item_dispatch_payload(item)),
                            )
                    task_item_store.update_item(item.id, worker_id=worker.id)
                    result.append({"item_id": item.id, "worker_id": worker.id})
                return result

            return {"object": "list", "data": await asyncio.to_thread(_assign)}

        async def _initialize_internal_worker(
            worker: Worker, *, user_id: str | None, app_state: Any
        ) -> Worker:
            assert session_creator is not None
            return await initialize_internal_worker(
                worker,
                worker_store=worker_store,
                session_creator=session_creator,
                app_state=app_state,
                user_id=user_id,
            )

        @router.post("/task-workers/{worker_id}/initialize", status_code=202)
        async def initialize_worker(request: Request, worker_id: str) -> dict[str, Any]:
            user_id = require_user(request, auth_provider)
            worker = await asyncio.to_thread(worker_store.get_worker, worker_id)
            if worker is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            await _get_task_or_404(worker.task_id, user_id)
            if worker.state in {"initializing", "idle", "busy"}:
                return _worker_to_response(worker)
            if worker.state not in {"uninitialized", "initialization_failed"}:
                raise OmnigentError(
                    f"Worker cannot initialize from state {worker.state}",
                    code=ErrorCode.CONFLICT,
                )
            if session_creator is None:
                raise OmnigentError(
                    "Worker initialization is unavailable", code=ErrorCode.INTERNAL_ERROR
                )
            claimed = await asyncio.to_thread(worker_store.claim_initialization, worker.id)
            if claimed is None:
                current = await asyncio.to_thread(worker_store.get_worker, worker.id)
                if current is None:
                    raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
                return _worker_to_response(current)
            task = asyncio.create_task(
                _initialize_internal_worker(claimed, user_id=user_id, app_state=request.app.state)
            )
            task.add_done_callback(lambda completed: completed.exception())
            return _worker_to_response(claimed)

        async def _control_worker(worker: Worker, event_type: str, request: Request) -> None:
            if worker.target_id is None:
                raise OmnigentError("Worker is not initialized", code=ErrorCode.CONFLICT)
            runner_router = getattr(request.app.state, "runner_router", None)
            routed = (
                runner_router.client_for_existing_conversation(worker.target_id)
                if runner_router is not None
                else None
            )
            if routed is None:
                raise OmnigentError("Worker runner is unavailable", code=ErrorCode.CONFLICT)
            response = await routed.client.post(
                f"/v1/sessions/{worker.target_id}/events", json={"type": event_type}, timeout=5.0
            )
            if response.status_code >= 400:
                raise OmnigentError("Worker control request failed", code=ErrorCode.CONFLICT)

        @router.post("/task-workers/{worker_id}/rebind")
        async def rebind_worker(request: Request, worker_id: str) -> dict[str, Any]:
            """Reconnect an internal Worker to its durable target session."""
            user_id = require_user(request, auth_provider)
            worker = await asyncio.to_thread(worker_store.get_worker, worker_id)
            if worker is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            await _get_task_or_404(worker.task_id, user_id)
            if worker.target_id is None:
                raise OmnigentError("Worker is not initialized", code=ErrorCode.CONFLICT)
            await _best_effort_ensure_conversation_runner(
                request,
                worker.target_id,
                conversation_store,
            )
            refreshed = await asyncio.to_thread(worker_store.get_worker, worker.id)
            assert refreshed is not None
            return _worker_to_response(refreshed)

        @router.post("/task-workers/{worker_id}/interrupt")
        async def interrupt_worker(request: Request, worker_id: str) -> dict[str, Any]:
            user_id = require_user(request, auth_provider)
            worker = await asyncio.to_thread(worker_store.get_worker, worker_id)
            if worker is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            await _get_task_or_404(worker.task_id, user_id)
            await _control_worker(worker, "interrupt", request)
            return _worker_to_response(worker)

        @router.delete("/task-workers/{worker_id}")
        async def terminate_worker(request: Request, worker_id: str) -> dict[str, Any]:
            user_id = require_user(request, auth_provider)
            worker = await asyncio.to_thread(worker_store.get_worker, worker_id)
            if worker is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            await _get_task_or_404(worker.task_id, user_id)
            if worker.target_id is not None:
                await _control_worker(worker, "stop_session", request)
            updated = await asyncio.to_thread(
                worker_store.update_worker,
                worker.id,
                state="terminated",
                needs_response=False,
            )
            assert updated is not None
            return _worker_to_response(updated)

        @router.post("/task-workers/{worker_id}/untrack")
        async def untrack_worker(request: Request, worker_id: str) -> dict[str, Any]:
            """Remove a worker from its task. Done items stay for audit; all
            other items are cancelled. The session keeps running as a regular
            session — only the PuppyGarden binding is removed.
            """
            user_id = require_user(request, auth_provider)
            worker = await asyncio.to_thread(worker_store.get_worker, worker_id)
            if worker is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            await _get_task_or_404(worker.task_id, user_id)

            # Cancel all non-done items for this worker.
            items = await asyncio.to_thread(task_item_store.list_items_for_task, worker.task_id)
            for item in items:
                if item.worker_id != worker_id:
                    continue
                if item.state == "done":
                    continue
                await asyncio.to_thread(task_item_store.update_item, item.id, state="cancelled")

            # Stop managed workers; external sessions keep running.
            if worker.kind != WORKER_KIND_EXTERNAL and worker.target_id is not None:
                await _control_worker(worker, "stop_session", request)

            updated = await asyncio.to_thread(
                worker_store.update_worker,
                worker.id,
                state="terminated",
                needs_response=False,
            )
            assert updated is not None
            return _worker_to_response(updated)

        @router.post("/task-workers/{worker_id}/reassign")
        async def reassign_worker(
            request: Request,
            worker_id: str,
            body: ReassignWorkerRequest,
        ) -> dict[str, Any]:
            """Move a worker to a different task. Done items stay on the
            source task; all other items are cancelled. A ``session.adopted``
            event wakes the target task's manager.
            """
            user_id = require_user(request, auth_provider)
            worker = await asyncio.to_thread(worker_store.get_worker, worker_id)
            if worker is None:
                raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
            await _get_task_or_404(worker.task_id, user_id)
            target_task = await _get_task_or_404(body.task_id, user_id)
            if target_task.state not in _LIVE_TASK_STATES:
                raise OmnigentError("Target task is not live", code=ErrorCode.CONFLICT)

            # Cancel all non-done items for this worker on the source task.
            items = await asyncio.to_thread(task_item_store.list_items_for_task, worker.task_id)
            for item in items:
                if item.worker_id != worker_id:
                    continue
                if item.state == "done":
                    continue
                await asyncio.to_thread(task_item_store.update_item, item.id, state="cancelled")

            # Move the worker.
            updated = await asyncio.to_thread(
                worker_store.update_worker,
                worker.id,
                task_id=body.task_id,
                state="idle",
                needs_response=False,
            )
            assert updated is not None

            # Emit session.adopted event to wake the target task's manager.
            adopted_event = await asyncio.to_thread(
                task_event_store.create_event,
                uuid.uuid4().hex,
                SESSION_ADOPTED,
                f"Session rebind: {worker.provider_name or worker_id}",
                source_key=worker.target_id,
                source="adoption",
                task_id=body.task_id,
                state="received",
                owner_user_id=target_task.owner_user_id,
            )
            await ingress_event(
                event=adopted_event,
                task_store=task_store,
                task_event_store=task_event_store,
                worker_store=worker_store,
                conversation_store=conversation_store,
                task_role_profile_store=task_role_profile_store,
                owner_user_id=target_task.owner_user_id,
                session_creator=session_creator,
                app_state=request.app.state,
                user_id=user_id,
            )
            return _worker_to_response(updated)

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
                    category=body.category,
                    title=body.title,
                    url=body.url,
                )

            created = await asyncio.to_thread(_create)
            return _asset_to_response(created)

        @router.delete("/agent-tasks/{task_id}/assets/{asset_id}")
        async def delete_task_asset_route(
            request: Request,
            task_id: str,
            asset_id: int,
        ) -> dict[str, Any]:
            """Detach an asset from one managed task."""
            user_id = require_user(request, auth_provider)
            await _get_task_or_404(task_id, user_id)

            def _delete() -> bool:
                return task_asset_store.delete_asset(task_id, asset_id)

            deleted = await asyncio.to_thread(_delete)
            if not deleted:
                raise OmnigentError("Task asset not found", code=ErrorCode.NOT_FOUND)
            return {
                "object": "agent.task.asset",
                "id": asset_id,
                "task_id": task_id,
                "deleted": True,
            }

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
            visible_items = [item for item in items if item.state != "cancelled"]
            return {
                "object": "list",
                "data": [_item_to_response(item) for item in visible_items],
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
            if task.state == "pending" and body.worker_id is not None:
                raise OmnigentError(
                    "Cannot assign a worker to an item on a pending task; "
                    "accept the package first",
                    code=ErrorCode.CONFLICT,
                )

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
                    worker_id=body.worker_id,
                    kind=body.kind,
                    event_ids=body.event_ids or None,
                    task_store=task_store,
                )
                if body.submit_for_user_ack and item.state == "draft":
                    return submit_item_for_user_ack(task_item_store, item.id)
                return item

            created = await asyncio.to_thread(_create)
            await asyncio.to_thread(task_store.bump_queue_rank, task_id)
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
                    worker_store=worker_store,
                )
                return _item_to_response(updated)
            # mark_done settles a human action without dispatch — short-circuit
            # before worker lookup/initialization like reject_item.
            if body.resolution == "mark_done":
                task = await _get_task_or_404(item.task_id, user_id)
                updated = await asyncio.to_thread(
                    complete_human_action,
                    item=item,
                    task=task,
                    task_item_store=task_item_store,
                    task_event_store=task_event_store,
                )
                return _item_to_response(updated)
            if body.resolution == "edit_and_dispatch" and body.edited_payload is None:
                raise OmnigentError("edited_payload is required", code=ErrorCode.INVALID_INPUT)
            # Accept/edit dispatch to a worker — refuse human actions here too so
            # the error names the kind instead of the missing worker lane.
            if item.kind == "human_action":
                raise OmnigentError(
                    "Human action items can only be marked done or dismissed",
                    code=ErrorCode.CONFLICT,
                )
            task = await _get_task_or_404(item.task_id, user_id)
            worker = worker_for_item(item, worker_store=worker_store)
            if worker is None:
                raise OmnigentError(
                    "Item must have an assigned Worker Provider before dispatch",
                    code=ErrorCode.CONFLICT,
                )
            if agent_queue_store is None and worker.target_id is None:
                if session_creator is None:
                    raise OmnigentError(
                        "Worker initialization is unavailable",
                        code=ErrorCode.INTERNAL_ERROR,
                    )
                claimed = await asyncio.to_thread(worker_store.claim_initialization, worker.id)
                if claimed is None:
                    current = await asyncio.to_thread(worker_store.get_worker, worker.id)
                    if current is None or current.target_id is None:
                        raise OmnigentError(
                            "Worker initialization is already in progress",
                            code=ErrorCode.CONFLICT,
                        )
                    worker = current
                else:
                    worker = await _initialize_internal_worker(
                        claimed,
                        user_id=user_id,
                        app_state=request.app.state,
                    )
                if worker.target_id is None:
                    raise OmnigentError(
                        worker.failure_reason or "Worker initialization failed",
                        code=ErrorCode.CONFLICT,
                    )
            worker_profile = await _manager_role_profile_for_task(task, user_id)
            manager_profile = await _manager_role_profile_for_task(task, user_id)

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

        @router.post("/task-items/{item_id}/edit-lease")
        async def acquire_task_item_edit_lease(
            request: Request, item_id: str, body: QueueHoldRequest
        ) -> dict[str, Any]:
            """Hold one queued delivery while its instructions or worker are edited."""
            user_id = require_user(request, auth_provider)
            item = await _get_item_or_404(item_id, user_id)
            if item.state != "queued":
                raise OmnigentError(
                    f"Cannot lease item in state {item.state!r}", code=ErrorCode.CONFLICT
                )
            if agent_queue_store is None:
                raise OmnigentError("Agent queue is unavailable", code=ErrorCode.INTERNAL_ERROR)
            queue_item = await asyncio.to_thread(
                agent_queue_store.find_open_item_for_source, item.id, role="worker"
            )
            if queue_item is None:
                raise OmnigentError("Queued delivery not found", code=ErrorCode.CONFLICT)
            token = body.token or uuid.uuid4().hex
            now = now_epoch()
            leased = await asyncio.to_thread(
                agent_queue_store.acquire_item_edit_lease,
                queue_item.id,
                token,
                now=now,
                ttl_s=90,
            )
            if leased is None:
                raise OmnigentError(
                    "Task item is already dispatching or being edited",
                    code=ErrorCode.CONFLICT,
                )
            return {
                "object": "agent.queue.item.edit_lease",
                "token": token,
                "expires_at": leased.edit_lease_expires_at,
            }

        @router.delete("/task-items/{item_id}/edit-lease/{token}")
        async def release_task_item_edit_lease(
            request: Request, item_id: str, token: str
        ) -> dict[str, Any]:
            """Release a matching task-item edit lease."""
            user_id = require_user(request, auth_provider)
            item = await _get_item_or_404(item_id, user_id)
            if agent_queue_store is None:
                raise OmnigentError("Agent queue is unavailable", code=ErrorCode.INTERNAL_ERROR)
            queue_item = await asyncio.to_thread(
                agent_queue_store.find_open_item_for_source, item.id, role="worker"
            )
            released = False
            if queue_item is not None:
                released = await asyncio.to_thread(
                    agent_queue_store.release_item_edit_lease, queue_item.id, token
                )
            return {"object": "agent.queue.item.edit_lease.release", "released": released}

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
                    worker_id=body.worker_id,
                    task_store=task_store,
                )

            if (
                item.state == "queued"
                and (body.instructions is not None or body.worker_id is not None)
                and not body.edit_lease_token
            ):
                raise OmnigentError(
                    "An active edit lease is required for queued item changes",
                    code=ErrorCode.CONFLICT,
                )
            if (
                item.state in {"queued", "interrupted", "dispatch_failed"}
                and body.instructions is not None
            ):
                if agent_queue_store is None:
                    raise OmnigentError(
                        "Agent queue is unavailable", code=ErrorCode.INTERNAL_ERROR
                    )
                queue_item = await asyncio.to_thread(
                    agent_queue_store.find_open_item_for_source, item.id, role="worker"
                )
                if queue_item is None and item.state == "queued":
                    raise OmnigentError("Queued delivery not found", code=ErrorCode.CONFLICT)
                if queue_item is not None:
                    proposed_payload = item_dispatch_payload(item)
                    proposed_payload["instructions"] = body.instructions
                    if body.internal_note is not None:
                        proposed_payload["internal_note"] = body.internal_note
                    queue_updated = await asyncio.to_thread(
                        agent_queue_store.update_item,
                        queue_item.id,
                        payload=json.dumps(proposed_payload),
                        edit_lease_token=body.edit_lease_token,
                    )
                    if queue_updated is None:
                        raise OmnigentError(
                            "The edit lease expired before the item was saved",
                            code=ErrorCode.CONFLICT,
                        )
            updated = await asyncio.to_thread(_patch)
            return _item_to_response(updated)

        @router.post("/task-items/{item_id}/cancel")
        async def cancel_task_item(request: Request, item_id: str) -> dict[str, Any]:
            """Remove queued or parked work from dispatch and hide it from the board."""
            user_id = require_user(request, auth_provider)
            item = await _get_item_or_404(item_id, user_id)
            if item.state not in {"queued", "interrupted", "dispatch_failed"}:
                raise OmnigentError(
                    f"Cannot cancel item in state {item.state!r}", code=ErrorCode.CONFLICT
                )
            if agent_queue_store is None:
                raise OmnigentError("Agent queue is unavailable", code=ErrorCode.INTERNAL_ERROR)
            queue_item = await asyncio.to_thread(
                agent_queue_store.find_open_item_for_source, item.id, role="worker"
            )
            if queue_item is None and item.state == "queued":
                raise OmnigentError("Queued delivery not found", code=ErrorCode.CONFLICT)
            if queue_item is not None:
                cancelled = await asyncio.to_thread(
                    agent_queue_store.cancel_item, queue_item.id, now=now_epoch()
                )
                if cancelled is None:
                    raise OmnigentError(
                        "Queued delivery could not be cancelled", code=ErrorCode.CONFLICT
                    )
            updated = await asyncio.to_thread(
                task_item_store.update_item, item.id, state="cancelled"
            )
            assert updated is not None
            return _item_to_response(updated)

        @router.post("/task-items/{item_id}/retry-dispatch")
        async def retry_task_item_dispatch(
            request: Request,
            item_id: str,
        ) -> dict[str, Any]:
            """Create a new queue delivery for a failed execution attempt."""
            user_id = require_user(request, auth_provider)
            item = await _get_item_or_404(item_id, user_id)
            task = await _get_task_or_404(item.task_id, user_id)
            if agent_queue_store is None:
                raise OmnigentError(
                    "Agent queue is unavailable",
                    code=ErrorCode.INTERNAL_ERROR,
                )
            if item.state in {"interrupted", "dispatch_failed"}:
                queue_item = await asyncio.to_thread(
                    agent_queue_store.find_open_item_for_source, item.id, role="worker"
                )
                if queue_item is None:
                    worker = worker_for_item(item, worker_store=worker_store)
                    if worker is None:
                        raise OmnigentError(
                            "Item must have a Worker before retry", code=ErrorCode.CONFLICT
                        )
                    retried = await asyncio.to_thread(
                        agent_queue_store.enqueue,
                        uuid.uuid4().hex,
                        AgentQueueKey(
                            role="worker",
                            owner_user_id=_effective_user_id(user_id),
                            scope_id=worker.id,
                        ),
                        kind="item.dispatch",
                        source_ids=[item.id],
                        payload=json.dumps(item_dispatch_payload(item)),
                    )
                else:
                    retried = await asyncio.to_thread(
                        agent_queue_store.retry_parked_item, queue_item.id, now=now_epoch()
                    )
                    if retried is None:
                        raise OmnigentError(
                            "Parked delivery could not be retried", code=ErrorCode.CONFLICT
                        )
                updated = await asyncio.to_thread(
                    task_item_store.update_item, item.id, state="queued"
                )
                assert updated is not None
                await asyncio.to_thread(task_store.bump_queue_rank, task.id)
                return {
                    "object": "agent.task.retry",
                    "task_item_id": item.id,
                    "agent_queue_item_id": retried.id,
                }
            if item.state != "queued":
                raise OmnigentError(
                    f"Cannot retry item in state {item.state!r}",
                    code=ErrorCode.CONFLICT,
                )
            attempts = await asyncio.to_thread(
                task_event_store.list_executions_for_item,
                item.id,
            )
            if not attempts or attempts[-1].status != "failed":
                raise OmnigentError(
                    "Only a failed execution can be retried",
                    code=ErrorCode.CONFLICT,
                )
            worker = worker_for_item(item, worker_store=worker_store)
            if worker is None or worker.target_id is None:
                raise OmnigentError(
                    "Item must have an initialized Worker before retry",
                    code=ErrorCode.CONFLICT,
                )

            from omnigent.server.routes.sessions.routes_events import (
                _retry_session_single_flight,
            )

            await _retry_session_single_flight(
                request=request,
                session_id=worker.target_id,
                conversation_store=conversation_store,
                runner_router=runner_router,
            )
            from omnigent.server.routes.sessions import _session_status_cache

            _session_status_cache[worker.target_id] = "idle"
            await asyncio.to_thread(
                conversation_store.set_session_live_status,
                worker.target_id,
                "idle",
            )
            await asyncio.to_thread(
                worker_store.update_worker,
                worker.id,
                state="idle",
                needs_response=False,
                failure_reason=None,
            )
            queue_item = await asyncio.to_thread(
                agent_queue_store.enqueue,
                uuid.uuid4().hex,
                AgentQueueKey(
                    role="worker",
                    owner_user_id=_effective_user_id(user_id),
                    scope_id=worker.id,
                ),
                kind="item.dispatch",
                source_ids=[item.id],
                payload=json.dumps(item_dispatch_payload(item)),
            )
            await asyncio.to_thread(task_store.bump_queue_rank, task.id)
            return {
                "object": "agent.task.retry",
                "task_item_id": item.id,
                "agent_queue_item_id": queue_item.id,
            }

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
            if worker is None or worker.target_id is None:
                raise OmnigentError(
                    "Item must have an initialized Worker before dispatch",
                    code=ErrorCode.CONFLICT,
                )
            worker_profile = await _manager_role_profile_for_task(task, user_id)
            payload = {
                "instructions": body.instructions or item.instructions or "",
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
            """Return ambiguous events and suggested clusters for manager reconcile."""
            require_user(request, auth_provider)
            return await asyncio.to_thread(
                build_ambiguous_inbox,
                task_event_store=task_event_store,
                task_item_store=task_item_store,
                task_store=task_store,
            )

        @router.post("/task-events/{event_id}/reroute")
        async def reroute_task_event(
            request: Request, event_id: str, body: RerouteEventRequest
        ) -> dict[str, Any]:
            """Move a misrouted event to a different task (cross-manager too).

            The receiving manager's packager picks it up as a fresh routed
            event. The event must be in ``routed`` state (already reconciled
            work is not moved).
            """
            user_id = require_user(request, auth_provider)
            target_task = await _get_task_or_404(body.task_id, user_id)
            event = await asyncio.to_thread(task_event_store.get_event, event_id)
            if event is None:
                raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
            if event.state != "routed":
                raise OmnigentError(
                    f"Cannot reroute an event in state {event.state!r}",
                    code=ErrorCode.CONFLICT,
                )
            if event.task_id == target_task.id:
                return {"id": event.id, "object": "task.event", "task_id": target_task.id}
            updated = await asyncio.to_thread(
                task_event_store.update_event,
                event_id,
                task_id=target_task.id,
            )
            return {
                "id": event.id,
                "object": "task.event",
                "task_id": target_task.id,
                "previous_task_id": event.task_id,
            }

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
            """Create a pending task package with manager-reconciled items."""
            user_id = require_user(request, auth_provider)
            task_id = _generate_task_id()
            tags = _tags_from_input(task_id, body.tags)
            all_event_ids = [event_id for item in body.items for event_id in item.event_ids]
            event_tags = collect_event_tags(
                all_event_ids,
                task_event_store=task_event_store,
            )

            task = await asyncio.to_thread(
                create_task_package,
                task_id=task_id,
                owner_user_id=_effective_user_id(user_id),
                title=body.title,
                goal=body.goal,
                description=body.description,
                internal_note=body.internal_note,
                tags=tags or task_tags_from_event_tags(task_id, event_tags),
                event_tags=event_tags,
                manager_conversation_id=body.manager_conversation_id,
                items=[
                    PackageItemSpec(
                        title=item.title,
                        event_ids=item.event_ids,
                        description=item.description,
                        instructions=item.instructions,
                        internal_note=item.internal_note,
                        item_id=item.item_id,
                        worker_id=item.worker_id,
                    )
                    for item in body.items
                ],
                task_store=task_store,
                task_item_store=task_item_store,
                task_event_store=task_event_store,
                worker_store=worker_store,
            )
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
                    worker_id=item.worker_id,
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
            if any(
                result is not None and spec.item_id is None
                for spec, result in zip(specs, results, strict=True)
            ):
                await asyncio.to_thread(task_store.bump_queue_rank, task_id)
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
            accepted = await _bootstrap_manager_for_task(request, accepted, user_id)
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
                proposed_task_goal=body.proposed_task_goal,
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

        @router.post("/agent-tasks/sessions/{session_id}/adopt")
        async def adopt_session_route(
            request: Request,
            session_id: str,
            body: AdoptSessionRequest,
        ) -> dict[str, Any]:
            """Directly adopt a session to a task (Worker binding).

            Replaces the old propose → accept → reject flow. The broker calls
            this when it triages a low-score orphan and decides which task to
            bind the session to.
            """
            user_id = require_user(request, auth_provider)
            await _require_session_or_404(session_id, user_id)
            task = await _get_task_or_404(body.task_id, user_id)
            conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
            if conv is None:
                raise OmnigentError("Session not found", code=ErrorCode.NOT_FOUND)

            def _adopt() -> str:
                return adopt_session_to_task(
                    session_id=session_id,
                    task=task,
                    conv=conv,
                    owner_user_id=_effective_user_id(user_id),
                )

            worker_id = await asyncio.to_thread(_adopt)
            return {
                "object": "agent.task.session_adoption",
                "session_id": session_id,
                "task_id": body.task_id,
                "worker_id": worker_id,
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
            worker = await asyncio.to_thread(worker_store.get_by_target_id, session_hint)
            return {
                "object": "agent.task.external_session_adoption",
                "session_hint": session_hint,
                "task_id": body.task_id,
                "worker_id": worker.id if worker is not None else None,
                "proposal": (
                    _event_to_response(proposal_event)
                    if proposal_event is not None
                    and proposal_event.event_type == "session.adoption"
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
            require_user(request, auth_provider)
            proposal = await asyncio.to_thread(
                find_open_external_adoption_proposal,
                task_event_store,
                session_hint,
            )
            dismissed = await asyncio.to_thread(
                reject_external_session_adoption,
                session_hint=session_hint,
                task_event_store=task_event_store,
                worker_store=worker_store,
                proposal_event=proposal,
            )
            return {
                "object": "agent.task.external_session_adoption_rejection",
                "session_hint": session_hint,
                "proposal": _event_to_response(dismissed) if dismissed is not None else None,
            }

    return router
