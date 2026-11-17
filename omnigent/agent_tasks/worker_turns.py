"""Read the recent turns of a worker's session, for the board search.

A turn is a user message and the agent's last reply to it. Meta messages, tool
calls and tool output are left out: they are long and noisy, and the search
needs only what the user and the agent said.
"""

from __future__ import annotations

from collections.abc import Sequence

from omnigent.agent_tasks.embeddings import MAX_EMBEDDED_CHARS
from omnigent.db.db_models import workspace_scope
from omnigent.entities.conversation import ConversationItem, MessageData
from omnigent.stores import ConversationStore

RECENT_TURNS = 3
_MAX_MESSAGES = 60
_MAX_MESSAGE_CHARS = MAX_EMBEDDED_CHARS // (2 * RECENT_TURNS)


def recent_turns_text(
    newest_first: Sequence[ConversationItem], *, turns: int = RECENT_TURNS
) -> str:
    """
    Return the last ``turns`` turns of a session as text, the oldest turn first.

    :param newest_first: The session's newest message items, the newest first.
    :param turns: How many turns to keep.
    :returns: Lines such as ``"User: ..."`` and ``"Agent: ..."``; empty when the
        session has no message with text.
    """
    collected: list[tuple[str, str]] = []
    reply = ""
    for item in newest_first:
        data = item.data
        if not isinstance(data, MessageData) or data.is_meta:
            continue
        text = _message_text(data)
        if not text:
            continue
        if data.role == "assistant":
            reply = reply or text
            continue
        collected.append((text, reply))
        reply = ""
        if len(collected) == turns:
            break
    lines = []
    for asked, answered in reversed(collected):
        lines.append(f"User: {asked}")
        if answered:
            lines.append(f"Agent: {answered}")
    return "\n\n".join(lines)


def _message_text(data: MessageData) -> str:
    texts = [block["text"] for block in data.content if isinstance(block.get("text"), str)]
    return "\n".join(texts).strip()[:_MAX_MESSAGE_CHARS]


class ConversationSessions:
    """Reads the workers' sessions from the conversation store, each in its workspace."""

    def __init__(self, conversations: ConversationStore) -> None:
        """
        :param conversations: Holds the sessions that the workers wrap.
        """
        self._conversations = conversations

    def changed_at(self, workspace_id: int, session_ids: Sequence[str]) -> dict[str, int]:
        """Return the ``updated_at`` of each session that still exists, by session id."""
        with workspace_scope(workspace_id):
            found = self._conversations.get_conversations(list(dict.fromkeys(session_ids)))
        return {session_id: conversation.updated_at for session_id, conversation in found.items()}

    def recent_turns(self, workspace_id: int, session_id: str) -> str:
        """Return the text of the session's last turns, the oldest turn first."""
        with workspace_scope(workspace_id):
            page = self._conversations.list_items(
                session_id, limit=_MAX_MESSAGES, order="desc", type="message"
            )
        return recent_turns_text(page.data)
