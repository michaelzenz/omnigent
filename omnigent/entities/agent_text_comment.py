"""A user's unsent comment on the text of a completed agent reply."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentTextComment:
    """
    One unsent review comment on a span of a completed agent message.

    Each user keeps their own batch of comments in a session. Sending the
    batch posts one message to the session and deletes the batch.

    :param id: Bare 32-char hex id.
    :param owner_user_id: The author, or ``None`` in single-user mode.
    :param conversation_id: The session, e.g. ``"0f1e..."``.
    :param conversation_item_id: The completed assistant message.
    :param start_offset: UTF-16 offset of the selection's start in the rendered text.
    :param end_offset: UTF-16 offset just after the selection's end.
    :param selected_text: The selected text.
    :param prefix_context: Text just before the selection, to find it again.
    :param suffix_context: Text just after the selection.
    :param body: The comment.
    :param created_at: Unix seconds.
    :param updated_at: Unix seconds of the last edit.
    """

    id: str
    owner_user_id: str | None
    conversation_id: str
    conversation_item_id: str
    start_offset: int
    end_offset: int
    selected_text: str
    prefix_context: str
    suffix_context: str
    body: str
    created_at: int
    updated_at: int
