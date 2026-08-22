"""Conversation label keys for session adoption routing."""

from __future__ import annotations

ROUTING_REPO_LABEL = "omnigent.task.routing_repo"
ROUTING_INTENT_LABEL = "omnigent.task.routing_intent"
ADOPTION_DISMISSED_LABEL = "omnigent.task.adoption_dismissed"
ROLE_LABEL = "omnigent.role"
BROKER_ROLE_VALUE = "task_broker"
SECRETARY_ROLE_VALUE = "task_secretary"


def presentation_labels_for_harness(harness: str | None) -> dict[str, str]:
    """Native coding-agent presentation labels (e.g. ``omnigent.wrapper``) for a harness.

    Returns an empty dict for non-native harnesses (notably the in-process
    ``openai-agents`` SDK harness), so role sessions on the SDK harness get no
    wrapper label — which is correct, since the composer's model picker is
    native-wrapper-only and the PuppyGarden dock surfaces its own switcher for
    them. Secretary/broker/worker/manager bootstraps share this so a role
    switched to a native harness picks up the composer picker consistently.
    """
    from omnigent.agent_tasks.constants import resolve_task_harness
    from omnigent.native_coding_agents import native_coding_agent_for_harness

    native_agent = native_coding_agent_for_harness(resolve_task_harness(harness or ""))
    return dict(native_agent.presentation_labels) if native_agent is not None else {}
