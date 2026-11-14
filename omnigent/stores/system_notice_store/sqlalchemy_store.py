"""SQLAlchemy-backed GlobalHub system notice store."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import ColumnElement, and_, desc, select, update
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlSystemNotice, current_workspace_id
from omnigent.db.enum_codecs import (
    decode_system_notice_body_kind,
    decode_system_notice_state,
    encode_system_notice_body_kind,
    encode_system_notice_state,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import SystemNotice
from omnigent.stores.system_notice_store import SystemNoticeStore

_ACTIVE = encode_system_notice_state("active")
_RESOLVED = encode_system_notice_state("resolved")


def _to_entity(row: SqlSystemNotice) -> SystemNotice:
    return SystemNotice(
        id=row.id,
        owner_user_id=row.owner_user_id,
        title=row.title,
        body_kind=decode_system_notice_body_kind(row.body_kind),
        state=decode_system_notice_state(row.state),
        created_at=row.created_at,
        body=row.body,
        actions=json.loads(row.actions) if row.actions else [],
        publisher_session_id=row.publisher_session_id,
        publisher_label=row.publisher_label,
        updated_at=row.updated_at,
        resolved_at=row.resolved_at,
    )


def _owned_active(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlSystemNotice.workspace_id == current_workspace_id(),
        SqlSystemNotice.owner_user_id == owner_user_id,
        SqlSystemNotice.state == _ACTIVE,
    )


class SqlAlchemySystemNoticeStore(SystemNoticeStore):
    """
    SQLAlchemy-backed implementation of :class:`SystemNoticeStore`.

    Every query is scoped by ``workspace_id`` and ``owner_user_id``. If two
    publishes race, the newest active notice is the owner's notice, and the
    next publish or dismissal resolves both.
    """

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: SQLAlchemy database URI, e.g.
            ``"sqlite:///chat.db"``.
        """
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.system_notice_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.system_notice_store",
            immediate=True,
        )

    def publish(
        self,
        notice_id: str,
        *,
        owner_user_id: str | None,
        title: str,
        body_kind: str = "markdown",
        body: str | None = None,
        actions: Sequence[Mapping[str, Any]] = (),
        publisher_session_id: str | None = None,
        publisher_label: str | None = None,
    ) -> SystemNotice:
        """Make a new notice the owner's active one, resolving the one it replaces."""
        now = now_epoch()
        body_kind_code = encode_system_notice_body_kind(body_kind)
        encoded_actions = (
            json.dumps([dict(action) for action in actions], ensure_ascii=False)
            if actions
            else None
        )

        def write(session: Session) -> SystemNotice:
            require_active_account(session, owner_user_id)
            session.execute(
                update(SqlSystemNotice)
                .where(_owned_active(owner_user_id))
                .values(state=_RESOLVED, resolved_at=now, updated_at=now)
            )
            row = SqlSystemNotice(
                id=notice_id,
                owner_user_id=owner_user_id,
                title=title,
                body_kind=body_kind_code,
                body=body,
                actions=encoded_actions,
                state=_ACTIVE,
                publisher_session_id=publisher_session_id,
                publisher_label=publisher_label,
                created_at=now,
                updated_at=None,
                resolved_at=None,
            )
            session.add(row)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "publish_system_notice", write)

    def get_active(self, *, owner_user_id: str | None) -> SystemNotice | None:
        """Return the owner's active notice, or ``None`` when there is none."""
        stmt = (
            select(SqlSystemNotice)
            .where(_owned_active(owner_user_id))
            .order_by(desc(SqlSystemNotice.created_at), desc(SqlSystemNotice.id))
            .limit(1)
        )
        with self._session("select_active_system_notice") as session:
            row = session.execute(stmt).scalars().first()
            return _to_entity(row) if row is not None else None

    def resolve_active(self, *, owner_user_id: str | None) -> SystemNotice | None:
        """Resolve the owner's active notice; return it, or ``None`` when there is none."""
        now = now_epoch()
        stmt = (
            select(SqlSystemNotice)
            .where(_owned_active(owner_user_id))
            .order_by(desc(SqlSystemNotice.created_at), desc(SqlSystemNotice.id))
        )

        def write(session: Session) -> SystemNotice | None:
            require_active_account(session, owner_user_id)
            rows = list(session.execute(stmt).scalars())
            for row in rows:
                row.state = _RESOLVED
                row.resolved_at = now
                row.updated_at = now
            return _to_entity(rows[0]) if rows else None

        return run_write_transaction(self._session_immediate, "resolve_system_notice", write)
