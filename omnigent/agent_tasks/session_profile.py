"""Build searchable profiles for orphan session adoption."""

from __future__ import annotations

from omnigent.agent_tasks.session_labels import (
    ROUTING_INTENT_LABEL,
    ROUTING_REPO_LABEL,
    ROUTING_SEARCH_TEXT_LABEL,
)
from omnigent.entities.conversation import Conversation
from omnigent.session_import import (
    IMPORT_EXTERNAL_SESSION_ID_LABEL_KEY,
    IMPORT_SOURCE_LABEL_KEY,
)


def build_auto_session_search_text(conv: Conversation) -> str:
    """Build a fallback routing profile from conversation metadata."""
    parts: list[str] = []
    if conv.title:
        parts.append(conv.title.strip())
    if conv.workspace:
        parts.append(conv.workspace.strip())
    import_source = conv.labels.get(IMPORT_SOURCE_LABEL_KEY)
    if import_source:
        parts.append(f"import:{import_source}")
    external_id = conv.labels.get(IMPORT_EXTERNAL_SESSION_ID_LABEL_KEY)
    if external_id:
        parts.append(f"external:{external_id}")
    return "\n".join(part for part in parts if part)


def resolve_session_search_text(conv: Conversation) -> str:
    """Return secretary-authored routing text, or a metadata fallback."""
    labeled = conv.labels.get(ROUTING_SEARCH_TEXT_LABEL, "").strip()
    if labeled:
        extra: list[str] = [labeled]
        repo = conv.labels.get(ROUTING_REPO_LABEL)
        if repo:
            extra.append(f"repo:{repo}")
        intent = conv.labels.get(ROUTING_INTENT_LABEL)
        if intent:
            extra.append(intent.strip())
        return "\n".join(extra)
    return build_auto_session_search_text(conv)
