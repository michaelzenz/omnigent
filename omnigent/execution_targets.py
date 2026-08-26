"""Identity and capabilities for built-in execution targets."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnigent.entities import Agent, Conversation
    from omnigent.spec.types import AgentSpec
    from omnigent.stores import AgentStore


ONIH_SETTINGS_KEY = "omniharness"
ONIH_OPENAI_AGENTS_TARGET = "onih-openai-agents"
ONIH_PI_TARGET = "onih-pi"
ONIH_TARGET_NAMES = frozenset({ONIH_OPENAI_AGENTS_TARGET, ONIH_PI_TARGET})
ONIH_DISPLAY_NAME = "Onih"
LEGACY_OMNIHARNESS_TARGET = "omniharness"

# Settings and telemetry keep their existing namespace. This alias is retained
# while call sites are migrated away from treating the settings key as a target.
OMNIHARNESS_AGENT_NAME = ONIH_SETTINGS_KEY
OMNIHARNESS_DISPLAY_NAME = ONIH_DISPLAY_NAME


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


# Compatibility names for internal callers. They intentionally do not recognize
# the legacy target: the old database row is hidden during the manual rollout.
def is_omniharness_agent(agent: Agent | None) -> bool:
    return is_onih_agent(agent)


def is_omniharness_spec(spec: AgentSpec | None) -> bool:
    return is_onih_spec(spec)


def conversation_uses_omniharness(
    conversation: Conversation | None,
    agent_store: AgentStore,
) -> bool:
    return conversation_uses_onih(conversation, agent_store)
