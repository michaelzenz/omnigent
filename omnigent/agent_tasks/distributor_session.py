"""Bootstrap helpers for the per-user task-distributor conversation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from omnigent.agent_tasks.agent_builtins import (
    TASK_DISTRIBUTOR_AGENT_NAME,
    resolve_task_agent_id,
)
from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.agent_tasks.constants import (
    DEFAULT_TASK_HARNESS,
    DEFAULT_TASK_MODEL,
    resolve_task_harness,
)
from omnigent._wrapper_labels import (
    CURSOR_NATIVE_WRAPPER_VALUE,
    UI_MODE_LABEL_KEY,
    UI_MODE_TERMINAL_VALUE,
    WRAPPER_LABEL_KEY,
)
from omnigent.agent_tasks.session_labels import (
    DISTRIBUTOR_ROLE_VALUE,
    SECRETARY_ROLE_LABEL,
)
from omnigent.db.utils import generate_task_id
from omnigent.entities import MessageData, NewConversationItem
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.secretary_profile_store import SecretaryProfileStore

_MANUAL_PATH = Path(__file__).resolve().parents[2] / "docs/agent-tasks/TASK_DISTRIBUTOR.md"

# In-memory distributor session binding; restart recovery deferred.
_DISTRIBUTOR_CONVERSATIONS: dict[str, str] = {}


@lru_cache(maxsize=1)
def load_distributor_manual() -> str:
    """Return the task distributor manual shipped with the repo."""
    return _MANUAL_PATH.read_text(encoding="utf-8")


def seed_distributor_manual(conversation_store: ConversationStore, conversation_id: str) -> None:
    """Append the distributor manual as hidden agent context."""
    manual = load_distributor_manual()
    text = f"[Task Distributor manual]\n\n{manual}"
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


def apply_distributor_session_labels(
    conversation_store: ConversationStore,
    conversation_id: str,
    *,
    harness: str,
) -> None:
    labels = {SECRETARY_ROLE_LABEL: DISTRIBUTOR_ROLE_VALUE}
    if harness == "cursor-native":
        labels[UI_MODE_LABEL_KEY] = UI_MODE_TERMINAL_VALUE
        labels[WRAPPER_LABEL_KEY] = CURSOR_NATIVE_WRAPPER_VALUE
    conversation_store.set_labels(conversation_id, labels)


def bootstrap_distributor_conversation(
    *,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    profile: UserSecretaryProfile,
    seed_manual: bool = True,
) -> str:
    """Create a distributor conversation using the secretary profile host/workspace."""
    params = resolve_bootstrap_params(
        host_id=profile.host_id,
        workspace=profile.workspace,
        harness=resolve_task_harness(DEFAULT_TASK_HARNESS),
        model=DEFAULT_TASK_MODEL,
        secretary_profile=profile,
    )
    agent_id = resolve_task_agent_id(agent_store, TASK_DISTRIBUTOR_AGENT_NAME)
    conversation = conversation_store.create_conversation(
        title="Task distributor",
        agent_id=agent_id,
        host_id=params.host_id,
        workspace=params.workspace,
    )
    conversation_store.update_conversation(
        conversation.id,
        harness_override=params.harness,
        model_override=params.model,
    )
    apply_distributor_session_labels(
        conversation_store,
        conversation.id,
        harness=params.harness,
    )
    if seed_manual:
        seed_distributor_manual(conversation_store, conversation.id)
    return conversation.id


def ensure_distributor_conversation(
    *,
    user_id: str,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    secretary_profile_store: SecretaryProfileStore,
) -> str | None:
    """Return a live distributor conversation for *user_id*, bootstrapping when needed."""
    existing_id = _DISTRIBUTOR_CONVERSATIONS.get(user_id)
    if existing_id is not None and conversation_store.get_conversation(existing_id) is not None:
        return existing_id

    profile = secretary_profile_store.get(user_id)
    if profile is None:
        return None

    conversation_id = bootstrap_distributor_conversation(
        conversation_store=conversation_store,
        agent_store=agent_store,
        profile=profile,
    )
    _DISTRIBUTOR_CONVERSATIONS[user_id] = conversation_id
    return conversation_id


def clear_distributor_conversation_cache() -> None:
    """Clear the in-memory distributor conversation map (tests)."""
    _DISTRIBUTOR_CONVERSATIONS.clear()
