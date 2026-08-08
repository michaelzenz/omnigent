"""Bootstrap helpers for the per-user task broker conversation."""

from __future__ import annotations

import logging
from typing import Any

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
    resolve_role_agent_profile_id,
)
from omnigent.agent_tasks.constants import (
    DEFAULT_TASK_WORKSPACE,
    resolve_task_harness,
)
from omnigent.agent_tasks.session_labels import BROKER_ROLE_VALUE, ROLE_LABEL
from omnigent.db.utils import generate_task_id, now_epoch
from omnigent.entities import MessageData, NewConversationItem
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.native_coding_agents import native_coding_agent_for_harness
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore, host_is_live
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.user_role_session_store import UserRoleSessionStore

_logger = logging.getLogger(__name__)

NO_HOST_AVAILABLE_MESSAGE = (
    "No host is available. Start a host with `omnigent host --server <url>` and try again."
)

PG_README_PATH = "docs/agent-tasks/README.md"
BROKER_MANUAL_PATH = "docs/agent-tasks/TASK_BROKER.md"

BROKER_SEED_PROMPT = (
    "You are the broker of the PuppyGarden task system. Read and follow "
    f"{PG_README_PATH} and {BROKER_MANUAL_PATH}."
)


def seed_broker_prompt(conversation_store: ConversationStore, conversation_id: str) -> None:
    """Append a short manual pointer as hidden agent context (not shown in the UI)."""
    item = NewConversationItem(
        type="message",
        response_id=generate_task_id(),
        data=MessageData(
            role="user",
            content=[{"type": "input_text", "text": BROKER_SEED_PROMPT}],
            is_meta=True,
        ),
    )
    conversation_store.append(conversation_id, [item])


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
) -> TaskRoleProfile:
    """Ensure a glossary role row exists, without requiring a live host."""
    existing = task_role_profile_store.get(role)
    if existing is not None:
        return existing

    host_id = None
    if host_store is not None:
        host_id = resolve_first_live_host_id(
            host_store,
            resolve_host_owner_user_id(auth_user_id),
        )

    agent_profile_id = resolve_role_agent_profile_id(agent_store, role)
    # harness/model omitted: the store seeds them from the role's packaged
    # defaults when it creates the row.
    return task_role_profile_store.upsert(
        role,
        agent_profile_id=agent_profile_id,
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
) -> TaskRoleProfile:
    """Load the role definition, auto-provisioning defaults on first use."""
    existing = task_role_profile_store.get(role)
    if existing is not None:
        return existing

    host_id = resolve_first_live_host_id(host_store, resolve_host_owner_user_id(auth_user_id))
    if host_id is None:
        raise OmnigentError(NO_HOST_AVAILABLE_MESSAGE, code=ErrorCode.INVALID_INPUT)

    agent_profile_id = resolve_role_agent_profile_id(agent_store, role)
    return task_role_profile_store.upsert(
        role,
        agent_profile_id=agent_profile_id,
        host_id=host_id,
        workspace=DEFAULT_TASK_WORKSPACE,
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
    seed_broker_prompt(conversation_store, conversation_id)
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
