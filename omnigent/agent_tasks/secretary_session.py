"""Bootstrap helpers for the per-user lightweight task secretary conversation.

The secretary is an on-demand Q&A agent: it remembers the task-system endpoints
and answers the user's questions. It has no packager, no queue, and no dispatch
handler — the user chats with it directly. Only session bootstrap lives here.
"""

from __future__ import annotations

import logging

from omnigent.agent_tasks.bootstrap import resolve_bootstrap_params
from omnigent.agent_tasks.constants import resolve_task_harness
from omnigent.agent_tasks.session_labels import ROLE_LABEL, SECRETARY_ROLE_VALUE
from omnigent.db.utils import generate_task_id
from omnigent.entities import MessageData, NewConversationItem
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.native_coding_agents import native_coding_agent_for_harness
from omnigent.runtime import get_agent_cache
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore

_logger = logging.getLogger(__name__)

SECRETARY_MANUAL_PATH = "docs/agent-tasks/TASK_SECRETARY.md"
API_REFERENCE_PATH = "docs/agent-tasks/API_REFERENCE.md"

SECRETARY_SEED_PROMPT = (
    "You are the task secretary of the PuppyGarden task system — a lightweight "
    "assistant that remembers the available endpoints and answers user questions "
    "about the task system. Read and follow " + SECRETARY_MANUAL_PATH + ". When you "
    "need to recall an endpoint's shape or parameters, read " + API_REFERENCE_PATH + "."
)


def seed_secretary_prompt(conversation_store: ConversationStore, conversation_id: str) -> None:
    """Append a short manual pointer as hidden agent context (not shown in the UI)."""
    item = NewConversationItem(
        type="message",
        response_id=generate_task_id(),
        data=MessageData(
            role="user",
            content=[{"type": "input_text", "text": SECRETARY_SEED_PROMPT}],
            is_meta=True,
        ),
    )
    conversation_store.append(conversation_id, [item])


def apply_secretary_session_labels(
    conversation_store: ConversationStore,
    conversation_id: str,
    *,
    harness: str,
) -> None:
    labels = {ROLE_LABEL: SECRETARY_ROLE_VALUE}
    native_agent = native_coding_agent_for_harness(resolve_task_harness(harness))
    if native_agent is not None:
        labels.update(native_agent.presentation_labels)
    conversation_store.set_labels(conversation_id, labels)


def _secretary_terminal_launch_args(
    agent_store: AgentStore,
    agent_id: str,
    *,
    harness: str,
) -> list[str] | None:
    """Derive native-terminal launch args from the packaged secretary spec."""
    from omnigent.server.routes.sessions import _derive_terminal_launch_args_from_spec

    agent = agent_store.get(agent_id)
    if agent is None:
        return None
    # Launch args are an optimisation; a role may point at an agent whose bundle
    # is missing or unreadable, and that must not fail the session bootstrap.
    try:
        loaded = get_agent_cache().load(
            agent.id,
            agent.bundle_location,
            expand_env=agent.session_id is None,
        )
    except (KeyError, OSError, RuntimeError):
        _logger.warning("secretary: could not load spec for agent %s", agent_id, exc_info=True)
        loaded = None
    if loaded is not None and loaded.spec is not None:
        return _derive_terminal_launch_args_from_spec(loaded.spec)
    # A profile may override the harness away from the packaged default.
    if resolve_task_harness(harness) == "claude-native":
        return ["--permission-mode", "auto"]
    return None


def bootstrap_secretary_conversation(
    *,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    profile: TaskRoleProfile,
    seed_prompt: bool = True,
) -> str:
    """Create a lightweight secretary conversation with harness/model defaults."""
    params = resolve_bootstrap_params(
        host_id=profile.host_id,
        workspace=profile.workspace,
        harness=profile.harness,
        model=profile.model,
        role_profile=profile,
    )
    terminal_launch_args = _secretary_terminal_launch_args(
        agent_store,
        params.agent_profile_id,
        harness=params.harness,
    )
    conversation = conversation_store.create_conversation(
        title="Task secretary",
        agent_id=params.agent_profile_id,
        host_id=params.host_id,
        workspace=params.workspace,
        terminal_launch_args=terminal_launch_args,
    )
    conversation_store.update_conversation(
        conversation.id,
        harness_override=params.harness,
        model_override=params.model,
        _unset_model_override=params.model is None,
    )
    apply_secretary_session_labels(
        conversation_store,
        conversation.id,
        harness=params.harness,
    )
    if seed_prompt:
        seed_secretary_prompt(conversation_store, conversation.id)
    return conversation.id
