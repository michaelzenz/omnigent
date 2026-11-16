"""SQLAlchemy-backed store of users' unsent comments on agent text."""

from __future__ import annotations

import builtins
from collections.abc import Collection

from sqlalchemy import ColumnElement, and_, asc, delete, select
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlAgentTextComment, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import AgentTextComment
from omnigent.stores.agent_text_comment_store import AgentTextCommentStore
from omnigent.util.uuid import InvalidUuidError, normalize_uuid, uuid_to_bytes


def _bare_id(value: str) -> str | None:
    """Return ``value`` as bare hex, or ``None``: an id that is not a UUID matches no comment."""
    try:
        return uuid_to_bytes(value).hex()
    except InvalidUuidError:
        return None


def _to_entity(row: SqlAgentTextComment) -> AgentTextComment:
    return AgentTextComment(
        id=row.id,
        owner_user_id=row.owner_user_id,
        conversation_id=row.conversation_id,
        conversation_item_id=row.conversation_item_id,
        start_offset=row.start_offset,
        end_offset=row.end_offset,
        selected_text=row.selected_text,
        prefix_context=row.prefix_context,
        suffix_context=row.suffix_context,
        body=row.body,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _owned(owner_user_id: str | None, conversation_id: str) -> ColumnElement[bool]:
    return and_(
        SqlAgentTextComment.workspace_id == current_workspace_id(),
        SqlAgentTextComment.conversation_id
        == (normalize_uuid(conversation_id) or conversation_id),
        SqlAgentTextComment.owner_user_id == owner_user_id,
    )


class SqlAlchemyAgentTextCommentStore(AgentTextCommentStore):
    """
    SQLAlchemy-backed implementation of :class:`AgentTextCommentStore`.

    Every query is scoped by ``workspace_id``, the session and ``owner_user_id``.
    """

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: SQLAlchemy database URI, e.g. ``"sqlite:///chat.db"``.
        """
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine, query_name_prefix="omnigent.agent_text_comment_store"
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine, query_name_prefix="omnigent.agent_text_comment_store", immediate=True
        )

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
        """Add a comment to the owner's batch in a session."""
        now = now_epoch()

        def write(session: Session) -> AgentTextComment:
            require_active_account(session, owner_user_id)
            row = SqlAgentTextComment(
                id=comment_id,
                owner_user_id=owner_user_id,
                conversation_id=normalize_uuid(conversation_id) or conversation_id,
                conversation_item_id=normalize_uuid(conversation_item_id) or conversation_item_id,
                start_offset=start_offset,
                end_offset=end_offset,
                selected_text=selected_text,
                prefix_context=prefix_context,
                suffix_context=suffix_context,
                body=body,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "add_agent_text_comment", write)

    def list(
        self, conversation_id: str, *, owner_user_id: str | None
    ) -> builtins.list[AgentTextComment]:
        """List the owner's batch in a session, oldest first."""
        stmt = (
            select(SqlAgentTextComment)
            .where(_owned(owner_user_id, conversation_id))
            .order_by(asc(SqlAgentTextComment.created_at), asc(SqlAgentTextComment.id))
        )
        with self._session("list_agent_text_comments") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def update(
        self, comment_id: str, *, owner_user_id: str | None, conversation_id: str, body: str
    ) -> AgentTextComment | None:
        """Replace the body of one of the owner's comments; ``None`` when missing or not theirs."""
        bare = _bare_id(comment_id)
        if bare is None:
            return None

        def write(session: Session) -> AgentTextComment | None:
            require_active_account(session, owner_user_id)
            row = session.execute(
                select(SqlAgentTextComment).where(
                    _owned(owner_user_id, conversation_id), SqlAgentTextComment.id == bare
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.body = body
            row.updated_at = now_epoch()
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "update_agent_text_comment", write)

    def delete(
        self, comment_ids: Collection[str], *, owner_user_id: str | None, conversation_id: str
    ) -> builtins.list[str]:
        """Delete the owner's comments among ``comment_ids``; return the ids deleted."""
        ids = [bare for comment_id in comment_ids if (bare := _bare_id(comment_id)) is not None]
        if not ids:
            return []

        def write(session: Session) -> builtins.list[str]:
            require_active_account(session, owner_user_id)
            owned = and_(_owned(owner_user_id, conversation_id), SqlAgentTextComment.id.in_(ids))
            found = list(session.execute(select(SqlAgentTextComment.id).where(owned)).scalars())
            if found:
                session.execute(delete(SqlAgentTextComment).where(owned))
            return found

        return run_write_transaction(self._session_immediate, "delete_agent_text_comments", write)
