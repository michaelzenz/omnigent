"""Identity and capabilities for built-in execution targets."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable

    from omnigent.entities import Agent, Conversation
    from omnigent.spec.types import AgentSpec
    from omnigent.stores import AgentStore


ONIH_SETTINGS_KEY = "omniharness"
ONIH_OPENAI_AGENTS_TARGET = "onih-openai-agents"
ONIH_PI_TARGET = "onih-pi"
ONIH_PUPPYGARDEN_TARGET = "onih-puppygarden"
ONIH_TARGET_NAMES = frozenset({ONIH_OPENAI_AGENTS_TARGET, ONIH_PI_TARGET, ONIH_PUPPYGARDEN_TARGET})
# Restricted read-only profile for PuppyGarden broker and manager
# roles. NOT in ONIH_TARGET_NAMES so it does not appear as a
# user-selectable Onih variant in the session picker — only used
# internally by the task system.
ONIH_PUPPYGARDEN_RO_TARGET = ONIH_PUPPYGARDEN_TARGET
ONIH_DISPLAY_NAME = "Onih"
# Settings and telemetry keep their existing namespace. This alias is retained
# while call sites are migrated away from treating the settings key as a target.
OMNIHARNESS_AGENT_NAME = ONIH_SETTINGS_KEY
OMNIHARNESS_DISPLAY_NAME = ONIH_DISPLAY_NAME


# Role predicates for the restricted read-only PuppyGarden profile.
# Imported lazily to keep the dependency one-directional.
_PUPPYGARDEN_RO_ROLE_KEYS: frozenset[str] = frozenset()
_is_manager_role_key: Callable[[str], bool] | None = None
_PUPPYGARDEN_RO_INITIALIZED = False


def _ensure_puppygarden_ro_predicates() -> None:
    global _PUPPYGARDEN_RO_ROLE_KEYS, _is_manager_role_key, _PUPPYGARDEN_RO_INITIALIZED
    if _PUPPYGARDEN_RO_INITIALIZED:
        return
    from omnigent.agent_tasks.role_keys import (
        TASK_BROKER_ROLE_KEY,
        is_manager_role_key,
    )

    _PUPPYGARDEN_RO_ROLE_KEYS = frozenset({TASK_BROKER_ROLE_KEY})
    _is_manager_role_key = is_manager_role_key
    _PUPPYGARDEN_RO_INITIALIZED = True


def execution_target_for_role(role: str) -> str:
    """Map a PuppyGarden role key to its execution-target agent name.

    Broker and all manager roles (``manager:default``, custom manager
    templates) use the restricted read-only ``onih-puppygarden`` profile
    (file read, search, MCP access, PuppyGarden APIs only). The secretary
    role and any other role use the general-purpose
    ``onih-openai-agents`` profile.
    """
    _ensure_puppygarden_ro_predicates()
    if role in _PUPPYGARDEN_RO_ROLE_KEYS or _is_manager_role_key(role):
        return ONIH_PUPPYGARDEN_RO_TARGET
    return ONIH_OPENAI_AGENTS_TARGET


def is_onih_target_name(name: str | None) -> bool:
    """Return whether *name* identifies one of the two Onih executors."""
    return name in ONIH_TARGET_NAMES


def is_onih_agent(agent: Agent | None) -> bool:
    """Return whether an agent belongs to a built-in Onih target."""
    if agent is None or agent.is_role:
        return False
    root_name = agent.name.split(" (switch ", 1)[0].split(" (fork ", 1)[0]
    return is_onih_target_name(root_name)


def is_onih_spec(spec: AgentSpec | None) -> bool:
    """Return whether a loaded spec belongs to the Onih target family."""
    return spec is not None and is_onih_target_name(spec.name)


def conversation_uses_onih(
    conversation: Conversation | None,
    agent_store: AgentStore,
) -> bool:
    """Resolve Onih from the bound target, never its adapter."""
    if conversation is None or conversation.agent_id is None:
        return False
    return is_onih_agent(agent_store.get(conversation.agent_id))


# Compatibility names for internal callers.
def is_omniharness_agent(agent: Agent | None) -> bool:
    return is_onih_agent(agent)


def is_omniharness_spec(spec: AgentSpec | None) -> bool:
    return is_onih_spec(spec)


def conversation_uses_omniharness(
    conversation: Conversation | None,
    agent_store: AgentStore,
) -> bool:
    return conversation_uses_onih(conversation, agent_store)
