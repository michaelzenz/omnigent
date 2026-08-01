"""Build routing tags for orphan session adoption."""

from __future__ import annotations

from omnigent.agent_tasks.session_labels import ROUTING_INTENT_LABEL, ROUTING_REPO_LABEL
from omnigent.entities import TaskEventTag
from omnigent.entities.conversation import Conversation


def resolve_session_routing_tags(session_id: str, conv: Conversation) -> list[TaskEventTag]:
    """Return structured routing tags written by the secretary on the session."""
    tags: list[TaskEventTag] = []
    repo = conv.labels.get(ROUTING_REPO_LABEL, "").strip()
    if repo:
        tags.append(TaskEventTag(event_id=session_id, tag_type="repo", tag=repo))
    intent = conv.labels.get(ROUTING_INTENT_LABEL, "").strip()
    if intent:
        tags.append(TaskEventTag(event_id=session_id, tag_type="intent", tag=intent))
    return tags
