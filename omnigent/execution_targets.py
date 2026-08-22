"""Identity and capabilities for built-in execution targets."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnigent.entities import Agent, Conversation
    from omnigent.spec.types import AgentSpec
    from omnigent.stores import AgentStore


OMNIHARNESS_AGENT_NAME = "omniharness"
OMNIHARNESS_DISPLAY_NAME = "OmniHarness"


def is_omniharness_agent(agent: Agent | None) -> bool:
    """Return whether an agent is the built-in OmniHarness target."""
    return agent is not None and not agent.is_role and agent.name == OMNIHARNESS_AGENT_NAME


def is_omniharness_spec(spec: AgentSpec | None) -> bool:
    """Return whether a loaded spec belongs to OmniHarness."""
    return spec is not None and spec.name == OMNIHARNESS_AGENT_NAME


def conversation_uses_omniharness(
    conversation: Conversation | None,
    agent_store: AgentStore,
) -> bool:
    """Resolve OmniHarness from the bound target, never its adapter."""
    if conversation is None or conversation.agent_id is None:
        return False
    return is_omniharness_agent(agent_store.get(conversation.agent_id))
