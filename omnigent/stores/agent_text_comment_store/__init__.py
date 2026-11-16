"""Persistence for users' unsent comments on agent text.

Each user keeps their own batch of comments in a session, so every read and
write is scoped to the owner and the session.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection

from omnigent.entities import AgentTextComment


class AgentTextCommentStore(ABC):
    """Users' unsent comments on spans of completed agent messages."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: SQLAlchemy database URI, e.g. ``"sqlite:///chat.db"``.
        """
        self.storage_location = storage_location

    @abstractmethod
    def add(
        self,
        comment_id: str,
        *,
        owner_user_id: str | None,
        conversation_id: str,
        conversation_item_id: str,
        start_offset: int,
        end_offset: int,
        selected_text: str,
        prefix_context: str,
        suffix_context: str,
        body: str,
    ) -> AgentTextComment:
        """
        Add a comment to the owner's batch in a session.

        :param comment_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: The author, or ``None`` in single-user mode.
        :param conversation_id: The session.
        :param conversation_item_id: The completed assistant message.
        :param start_offset: UTF-16 offset of the selection's start.
        :param end_offset: UTF-16 offset just after the selection's end.
        :param selected_text: The selected text.
        :param prefix_context: Text just before the selection.
        :param suffix_context: Text just after the selection.
        :param body: The comment.
        :returns: The new comment.
        """

    @abstractmethod
    def list(
        self, conversation_id: str, *, owner_user_id: str | None
    ) -> builtins.list[AgentTextComment]:
        """List the owner's batch in a session, oldest first."""

    @abstractmethod
    def update(
        self, comment_id: str, *, owner_user_id: str | None, conversation_id: str, body: str
    ) -> AgentTextComment | None:
        """Replace the body of one of the owner's comments; ``None`` when missing or not theirs."""

    @abstractmethod
    def delete(
        self, comment_ids: Collection[str], *, owner_user_id: str | None, conversation_id: str
    ) -> builtins.list[str]:
        """Delete the owner's comments among ``comment_ids``; return the ids deleted."""
