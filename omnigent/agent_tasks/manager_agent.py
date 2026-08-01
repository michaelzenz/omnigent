"""Resolve the task-manager agent profile id for routing and bindings."""

from __future__ import annotations

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_AGENT_NAME, resolve_task_agent_id
from omnigent.entities import Task
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore


def resolve_agent_profile_id(
    agent_store: AgentStore,
    agent_profile_id: str | None = None,
) -> str:
    """Return an explicit profile id or the built-in task-manager agent."""
    if agent_profile_id:
        return agent_profile_id
    return resolve_task_agent_id(agent_store, TASK_MANAGER_AGENT_NAME)


def resolve_manager_profile_id(
    agent_store: AgentStore,
    *,
    agent_profile_id: str | None = None,
    conversation_store: ConversationStore | None = None,
    manager_conversation_id: str | None = None,
) -> str:
    """Return the manager profile id for a session or task."""
    if agent_profile_id:
        return agent_profile_id
    if conversation_store is not None and manager_conversation_id:
        conv = conversation_store.get_conversation(manager_conversation_id)
        if conv is not None and conv.agent_id:
            return conv.agent_id
    return resolve_task_agent_id(agent_store, TASK_MANAGER_AGENT_NAME)


def resolve_manager_profile_id_for_task(
    task: Task,
    *,
    agent_store: AgentStore,
    conversation_store: ConversationStore,
) -> str:
    """Return the manager profile id for a managed task."""
    return resolve_manager_profile_id(
        agent_store,
        agent_profile_id=task.agent_profile_id,
        conversation_store=conversation_store,
        manager_conversation_id=task.manager_conversation_id,
    )
