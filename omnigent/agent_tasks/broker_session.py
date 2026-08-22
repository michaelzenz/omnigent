"""Bootstrap helpers for the per-user task broker conversation."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from omnigent.agent_tasks.agent_builtins import TASK_BROKER_ROLE
from omnigent.agent_tasks.constants import (
    DEFAULT_TASK_WORKSPACE,
    resolve_task_harness,
)
from omnigent.agent_tasks.role_keys import (
    TASK_SECRETARY_ROLE_KEY,
    is_manager_role_key,
    role_profile_title,
)
from omnigent.agent_tasks.session_labels import BROKER_ROLE_VALUE, ROLE_LABEL
from omnigent.db.utils import now_epoch
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.execution_targets import OMNIHARNESS_AGENT_NAME
from omnigent.native_coding_agents import native_coding_agent_for_harness
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore, host_is_live
from omnigent.stores.prompt_profile_store import PromptProfileStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.user_role_session_store import UserRoleSessionStore

_logger = logging.getLogger(__name__)

_ROLE_MANUALS = {
    TASK_BROKER_ROLE: (
        "You are the task broker of PuppyGarden. Read host.puppygarden.root from "
        "~/.omnigent/config.yaml and follow its docs/TASK_BROKER.md manual."
    ),
    TASK_SECRETARY_ROLE_KEY: (
        "You are the task secretary of PuppyGarden. Read host.puppygarden.root from "
        "~/.omnigent/config.yaml and follow its docs/TASK_SECRETARY.md manual."
    ),
}


def _role_manual(role: str) -> str:
    if is_manager_role_key(role):
        return (
            "You are a PuppyGarden task manager. Read host.puppygarden.root from "
            "~/.omnigent/config.yaml and follow its docs/TASK_MANAGER.md manual."
        )
    return _ROLE_MANUALS.get(role, f"Follow the PuppyGarden manual for role {role}.")


def _ensure_role_prompt_profile(
    role: str,
    store: PromptProfileStore,
    existing_profile_id: str | None,
) -> str:
    if existing_profile_id is not None and store.get(existing_profile_id) is not None:
        return existing_profile_id
    profile = store.create(
        uuid.uuid4().hex,
        f"PuppyGarden · {role_profile_title(role)}",
        _role_manual(role),
        description=f"PuppyGarden manual for {role_profile_title(role)}",
        visible=False,
    )
    return profile.id


NO_HOST_AVAILABLE_MESSAGE = (
    "No host is available. Start a host with `omnigent host --server <url>` and try again."
)


def resolve_host_owner_user_id(auth_user_id: str | None) -> str:
    """Map an authenticated user id to the host-store owner key."""
    return auth_user_id if auth_user_id is not None else RESERVED_USER_LOCAL


def resolve_first_live_host_id(host_store: HostStore, owner: str) -> str | None:
    """Return the most recently seen live user-connected host for *owner*."""
    now = now_epoch()
    for host in host_store.list_hosts(owner):
        if host.sandbox_provider is not None:
            continue
        if host_is_live(host, now=now):
            return host.host_id
    return None


def ensure_role_profile(
    *,
    role: str,
    auth_user_id: str | None,
    task_role_profile_store: TaskRoleProfileStore,
    agent_store: AgentStore,
    host_store: HostStore | None = None,
    prompt_profile_store: PromptProfileStore | None = None,
) -> TaskRoleProfile:
    """Ensure a glossary role row exists, without requiring a live host."""
    existing = task_role_profile_store.get(role)
    if (
        existing is not None
        and (existing.prompt_profile_id or prompt_profile_store is None)
        and (host_store is None or existing.host_id is not None)
    ):
        return existing

    host_id = existing.host_id if existing is not None else None
    if host_store is not None:
        host_id = resolve_first_live_host_id(
            host_store,
            resolve_host_owner_user_id(auth_user_id),
        )

    omniharness = agent_store.get_by_name(OMNIHARNESS_AGENT_NAME)
    if omniharness is None:
        raise OmnigentError(
            "OmniHarness execution target is unavailable", code=ErrorCode.NOT_FOUND
        )
    prompt_profile_id = (
        _ensure_role_prompt_profile(
            role, prompt_profile_store, existing.prompt_profile_id if existing else None
        )
        if prompt_profile_store is not None
        else None
    )
    return task_role_profile_store.upsert(
        role,
        agent_profile_id=omniharness.id,
        prompt_profile_id=prompt_profile_id,
        harness="openai-agents",
        host_id=host_id,
        workspace=DEFAULT_TASK_WORKSPACE,
    )


def get_or_create_role_profile(
    *,
    role: str,
    auth_user_id: str | None,
    task_role_profile_store: TaskRoleProfileStore,
    host_store: HostStore,
    agent_store: AgentStore,
    prompt_profile_store: PromptProfileStore | None = None,
) -> TaskRoleProfile:
    """Load the role definition, auto-provisioning defaults on first use."""
    existing = task_role_profile_store.get(role)
    if (
        existing is not None
        and (existing.prompt_profile_id or prompt_profile_store is None)
        and existing.host_id is not None
    ):
        return existing

    host_id = resolve_first_live_host_id(host_store, resolve_host_owner_user_id(auth_user_id))
    if host_id is None:
        raise OmnigentError(NO_HOST_AVAILABLE_MESSAGE, code=ErrorCode.INVALID_INPUT)

    return ensure_role_profile(
        role=role,
        auth_user_id=auth_user_id,
        task_role_profile_store=task_role_profile_store,
        agent_store=agent_store,
        host_store=host_store,
        prompt_profile_store=prompt_profile_store,
    )


def _broker_labels_for_profile(profile: TaskRoleProfile) -> dict[str, str]:
    """Build the role + native presentation labels for a broker session."""
    labels = {ROLE_LABEL: BROKER_ROLE_VALUE}
    native_agent = native_coding_agent_for_harness(resolve_task_harness(profile.harness or ""))
    if native_agent is not None:
        labels.update(native_agent.presentation_labels)
    return labels


async def ensure_broker_session(
    *,
    owner_user_id: str,
    task_role_profile_store: TaskRoleProfileStore,
    user_role_session_store: UserRoleSessionStore,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    host_store: HostStore,
    session_creator: Any,
    app_state: Any,
    prompt_profile_store: PromptProfileStore | None = None,
) -> str | None:
    """Return the owner's live broker conversation, creating one on demand.

    The broker has no UI surface, so nothing bootstraps its session the way the
    secretary rail does. Callers that need a broker to talk to provision it here
    instead. Returns ``None`` when no live host is available yet, so the caller
    leaves the work queued rather than failing.

    The session goes through the full ``create_session_internal`` path (workspace
    validation, runner launch, permissions, adoption) — the same path as
    ``POST /v1/sessions``.
    """
    auth_user_id = None if owner_user_id == "__anonymous__" else owner_user_id
    try:
        profile = get_or_create_role_profile(
            role=TASK_BROKER_ROLE,
            auth_user_id=auth_user_id,
            task_role_profile_store=task_role_profile_store,
            host_store=host_store,
            agent_store=agent_store,
            prompt_profile_store=prompt_profile_store,
        )
    except OmnigentError:
        return None

    session = user_role_session_store.get(owner_user_id, TASK_BROKER_ROLE)
    if session is not None and session.conversation_id is not None:
        if conversation_store.get_conversation(session.conversation_id) is not None:
            return session.conversation_id

    from omnigent.agent_tasks.bootstrap import build_role_session_request
    from omnigent.server.routes.sessions import _make_internal_request

    request = _make_internal_request(app_state)
    labels = _broker_labels_for_profile(profile)
    body = build_role_session_request(
        profile,
        title="Task broker",
        labels=labels,
    )
    resp = await session_creator(
        body=body,
        request=request,
        user_id=auth_user_id,
    )
    conversation_id = resp.id
    user_role_session_store.set_conversation(owner_user_id, TASK_BROKER_ROLE, conversation_id)
    return conversation_id


def apply_broker_session_labels(
    conversation_store: ConversationStore,
    conversation_id: str,
    *,
    harness: str,
) -> None:
    labels = {ROLE_LABEL: BROKER_ROLE_VALUE}
    native_agent = native_coding_agent_for_harness(resolve_task_harness(harness))
    if native_agent is not None:
        labels.update(native_agent.presentation_labels)
    conversation_store.set_labels(conversation_id, labels)
