"""Bootstrap helpers for the per-user task secretary conversation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from omnigent.agent_tasks.agent_builtins import (
    TASK_SECRETARY_AGENT_NAME,
    resolve_task_agent_id,
)
from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.agent_tasks.constants import (
    DEFAULT_SECRETARY_HARNESS,
    DEFAULT_SECRETARY_MODEL,
    DEFAULT_TASK_WORKSPACE,
    resolve_task_harness,
)
from omnigent._wrapper_labels import (
    CURSOR_NATIVE_WRAPPER_VALUE,
    UI_MODE_LABEL_KEY,
    UI_MODE_TERMINAL_VALUE,
    WRAPPER_LABEL_KEY,
)
from omnigent.agent_tasks.session_labels import SECRETARY_ROLE_LABEL, SECRETARY_ROLE_VALUE
from omnigent.db.utils import generate_task_id, now_epoch
from omnigent.entities import MessageData, NewConversationItem
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore, host_is_live
from omnigent.stores.secretary_profile_store import SecretaryProfileStore

NO_HOST_AVAILABLE_MESSAGE = (
    "No host is available. Start a host with `omnigent host --server <url>` and try again."
)

_MANUAL_PATH = Path(__file__).resolve().parents[2] / "docs/agent-tasks/TASK_SECRETARY.md"


@lru_cache(maxsize=1)
def load_secretary_manual() -> str:
    """Return the task secretary manual shipped with the repo."""
    return _MANUAL_PATH.read_text(encoding="utf-8")


def seed_secretary_manual(conversation_store: ConversationStore, conversation_id: str) -> None:
    """Append the secretary manual as hidden agent context (not shown in the UI)."""
    manual = load_secretary_manual()
    text = f"[Task Secretary manual]\n\n{manual}"
    item = NewConversationItem(
        type="message",
        response_id=generate_task_id(),
        data=MessageData(
            role="user",
            content=[{"type": "input_text", "text": text}],
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


def get_or_create_secretary_profile(
    *,
    profile_user_id: str,
    auth_user_id: str | None,
    secretary_profile_store: SecretaryProfileStore,
    host_store: HostStore,
    agent_store: AgentStore,
) -> UserSecretaryProfile:
    """Load the secretary profile, auto-provisioning defaults on first use."""
    existing = secretary_profile_store.get(profile_user_id)
    if existing is not None:
        return existing

    host_id = resolve_first_live_host_id(host_store, resolve_host_owner_user_id(auth_user_id))
    if host_id is None:
        raise OmnigentError(NO_HOST_AVAILABLE_MESSAGE, code=ErrorCode.INVALID_INPUT)

    agent_id = resolve_task_agent_id(agent_store, TASK_SECRETARY_AGENT_NAME)
    return secretary_profile_store.upsert(
        profile_user_id,
        agent_id=agent_id,
        host_id=host_id,
        harness=DEFAULT_SECRETARY_HARNESS,
        model=DEFAULT_SECRETARY_MODEL,
        workspace=DEFAULT_TASK_WORKSPACE,
    )


def resolve_secretary_agent_id(
    agent_store: AgentStore,
    profile: UserSecretaryProfile,
) -> str:
    """Prefer the packaged task-secretary builtin over a stale profile agent id."""
    return resolve_task_agent_id(
        agent_store,
        TASK_SECRETARY_AGENT_NAME,
        fallback_agent_id=profile.agent_id,
    )


def apply_secretary_session_labels(
    conversation_store: ConversationStore,
    conversation_id: str,
    *,
    harness: str,
) -> None:
    labels = {SECRETARY_ROLE_LABEL: SECRETARY_ROLE_VALUE}
    if harness == "cursor-native":
        labels[UI_MODE_LABEL_KEY] = UI_MODE_TERMINAL_VALUE
        labels[WRAPPER_LABEL_KEY] = CURSOR_NATIVE_WRAPPER_VALUE
    conversation_store.set_labels(conversation_id, labels)


def bootstrap_secretary_conversation(
    *,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    profile: UserSecretaryProfile,
    seed_manual: bool = True,
) -> str:
    """Create a secretary conversation with harness/model defaults and optional manual seed."""
    params = resolve_bootstrap_params(
        host_id=profile.host_id,
        workspace=profile.workspace,
        harness=resolve_task_harness(profile.harness),
        model=profile.model,
        secretary_profile=profile,
    )
    agent_id = resolve_secretary_agent_id(agent_store, profile)
    conversation = conversation_store.create_conversation(
        title="Task secretary",
        agent_id=agent_id,
        host_id=params.host_id,
        workspace=params.workspace,
    )
    conversation_store.update_conversation(
        conversation.id,
        harness_override=params.harness,
        model_override=params.model,
    )
    apply_secretary_session_labels(
        conversation_store,
        conversation.id,
        harness=params.harness,
    )
    if seed_manual:
        seed_secretary_manual(conversation_store, conversation.id)
    return conversation.id
