"""SQLAlchemy-backed GlobalHub delivery queue store."""

from __future__ import annotations

import builtins
import json
import threading
import time
from collections.abc import Collection, Mapping
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, asc, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, aliased

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlAgentQueueItem, SqlTaskEvent, current_workspace_id
from omnigent.db.enum_codecs import (
    decode_agent_queue_item_kind,
    decode_agent_queue_item_state,
    encode_agent_queue_item_kind,
    encode_agent_queue_item_state,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import AgentQueueItem
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.util.uuid import normalize_uuid

_QUEUED = encode_agent_queue_item_state("queued")
_SENDING = encode_agent_queue_item_state("sending")
_SENT = encode_agent_queue_item_state("sent")
_DONE = encode_agent_queue_item_state("done")

_seq_lock = threading.Lock()
_last_seq = 0


def _next_seq() -> int:
    """Return a nanosecond clock reading, strictly increasing within this process."""
    global _last_seq
    with _seq_lock:
        _last_seq = max(time.time_ns(), _last_seq + 1)
        return _last_seq


def _to_entity(row: SqlAgentQueueItem) -> AgentQueueItem:
    return AgentQueueItem(
        id=row.id,
        owner_user_id=row.owner_user_id,
        target_session_id=row.target_session_id,
        kind=decode_agent_queue_item_kind(row.kind),
        state=decode_agent_queue_item_state(row.state),
        seq=row.seq,
        created_at=row.created_at,
        payload=json.loads(row.payload) if row.payload else {},
        attempts=row.attempts,
        not_before=row.not_before,
        last_error=row.last_error,
        updated_at=row.updated_at,
        sent_at=row.sent_at,
        done_at=row.done_at,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlAgentQueueItem.workspace_id == current_workspace_id(),
        SqlAgentQueueItem.owner_user_id == owner_user_id,
    )


def _owned_row(
    session: Session, item_id: str, owner_user_id: str | None
) -> SqlAgentQueueItem | None:
    row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
    return row if row is not None and row.owner_user_id == owner_user_id else None


class SqlAlchemyAgentQueueStore(AgentQueueStore):
    """
    SQLAlchemy-backed implementation of :class:`AgentQueueStore`.

    Every query is scoped by ``workspace_id``, and all but the dispatcher's
    claim by ``owner_user_id``.
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
            query_name_prefix="omnigent.agent_queue_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.agent_queue_store",
            immediate=True,
        )

    def enqueue(
        self,
        item_id: str,
        *,
        owner_user_id: str | None,
        target_session_id: str,
        kind: str,
        payload: Mapping[str, Any] | None = None,
        event_ids: Collection[str] = (),
    ) -> AgentQueueItem | None:
        """Queue a message for a session; the given events then point at it, all or none."""
        wanted = list(
            dict.fromkeys(normalize_uuid(event_id) or event_id for event_id in event_ids)
        )
        kind_code = encode_agent_queue_item_kind(kind)
        now = now_epoch()

        def write(session: Session) -> AgentQueueItem | None:
            require_active_account(session, owner_user_id)
            events: builtins.list[SqlTaskEvent] = []
            if wanted:
                stmt = select(SqlTaskEvent).where(
                    SqlTaskEvent.workspace_id == current_workspace_id(),
                    SqlTaskEvent.owner_user_id == owner_user_id,
                    SqlTaskEvent.id.in_(wanted),
                )
                events = list(session.execute(stmt).scalars())
                if len(events) != len(wanted):
                    return None
                held_by = {event.queue_item_id for event in events} - {None}
                if held_by:
                    open_item = session.execute(
                        select(SqlAgentQueueItem.id).where(
                            SqlAgentQueueItem.workspace_id == current_workspace_id(),
                            SqlAgentQueueItem.id.in_(held_by),
                            SqlAgentQueueItem.state != _DONE,
                        )
                    ).first()
                    if open_item is not None:
                        return None
            row = SqlAgentQueueItem(
                id=item_id,
                owner_user_id=owner_user_id,
                target_session_id=target_session_id,
                kind=kind_code,
                state=_QUEUED,
                seq=_next_seq(),
                payload=json.dumps(dict(payload), ensure_ascii=False) if payload else None,
                attempts=0,
                not_before=None,
                last_error=None,
                created_at=now,
                updated_at=None,
                sent_at=None,
                done_at=None,
            )
            session.add(row)
            for event in events:
                event.queue_item_id = item_id
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "enqueue_agent_queue_item", write)

    def get(self, item_id: str, *, owner_user_id: str | None) -> AgentQueueItem | None:
        """Return the owner's item, or ``None`` when missing or someone else's."""
        with self._session("select_agent_queue_item_by_id") as session:
            row = _owned_row(session, item_id, owner_user_id)
            return _to_entity(row) if row is not None else None

    def list(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        target_session_id: str | None = None,
    ) -> builtins.list[AgentQueueItem]:
        """List the owner's items in arrival order."""
        stmt = select(SqlAgentQueueItem).where(_owned(owner_user_id))
        if states is not None:
            stmt = stmt.where(
                SqlAgentQueueItem.state.in_(
                    [encode_agent_queue_item_state(state) for state in states]
                )
            )
        if target_session_id is not None:
            stmt = stmt.where(SqlAgentQueueItem.target_session_id == target_session_id)
        stmt = stmt.order_by(asc(SqlAgentQueueItem.seq), asc(SqlAgentQueueItem.id))
        with self._session("list_agent_queue_items") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def claim_due(self, *, now: int, limit: int = 100) -> builtins.list[AgentQueueItem]:
        """Claim the items ready to send, for the dispatcher, and mark them ``sending``."""
        item = SqlAgentQueueItem
        other = aliased(SqlAgentQueueItem)
        workspace_id = current_workspace_id()
        same_session = and_(
            other.workspace_id == workspace_id,
            other.target_session_id == item.target_session_id,
        )
        in_flight = select(other.id).where(same_session, other.state.in_((_SENDING, _SENT)))
        queued_earlier = select(other.id).where(
            same_session, other.state == _QUEUED, other.seq < item.seq
        )
        due = (
            select(item)
            .where(
                item.workspace_id == workspace_id,
                item.state == _QUEUED,
                or_(item.not_before.is_(None), item.not_before <= now),
                ~in_flight.exists(),
                ~queued_earlier.exists(),
            )
            .order_by(asc(item.seq), asc(item.id))
            .limit(limit)
        )

        def write(session: Session) -> builtins.list[AgentQueueItem]:
            rows = list(session.execute(due.with_for_update()).scalars())
            for row in rows:
                row.state = _SENDING
                row.updated_at = now
            return [_to_entity(row) for row in rows]

        # Most passes find nothing; check without taking SQLite's write lock.
        with self._session("find_due_agent_queue_items") as session:
            if session.execute(due.with_only_columns(item.id).limit(1)).first() is None:
                return []
        return run_write_transaction(self._session_immediate, "claim_due_agent_queue_items", write)

    def release_stale_claims(self, *, claimed_before: int) -> int:
        """Put back every owner's items claimed before ``claimed_before`` and never settled."""
        now = now_epoch()

        def write(session: Session) -> int:
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    update(SqlAgentQueueItem)
                    .where(
                        SqlAgentQueueItem.workspace_id == current_workspace_id(),
                        SqlAgentQueueItem.state == _SENDING,
                        SqlAgentQueueItem.updated_at < claimed_before,
                    )
                    .values(state=_QUEUED, updated_at=now)
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount

        return run_write_transaction(
            self._session_immediate, "release_stale_agent_queue_claims", write
        )

    def list_sent(self) -> builtins.list[AgentQueueItem]:
        """List every owner's ``sent`` items in arrival order, for the dispatcher."""
        stmt = (
            select(SqlAgentQueueItem)
            .where(
                SqlAgentQueueItem.workspace_id == current_workspace_id(),
                SqlAgentQueueItem.state == _SENT,
            )
            .order_by(asc(SqlAgentQueueItem.seq), asc(SqlAgentQueueItem.id))
        )
        with self._session("list_sent_agent_queue_items") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def mark_done_for_session(self, session_id: str) -> int:
        """Record that the turn handling the item sent to ``session_id`` ended."""
        now = now_epoch()

        def write(session: Session) -> int:
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    update(SqlAgentQueueItem)
                    .where(
                        SqlAgentQueueItem.workspace_id == current_workspace_id(),
                        SqlAgentQueueItem.target_session_id == session_id,
                        SqlAgentQueueItem.state == _SENT,
                    )
                    .values(state=_DONE, done_at=now, updated_at=now)
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount

        return run_write_transaction(
            self._session_immediate, "mark_session_agent_queue_item_done", write
        )

    def defer(
        self, item_id: str, *, owner_user_id: str | None, not_before: int
    ) -> AgentQueueItem | None:
        """Put a claimed item back without counting a failed attempt."""
        now = now_epoch()

        def write(session: Session) -> AgentQueueItem | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, item_id, owner_user_id)
            if row is None or row.state != _SENDING:
                return None
            row.state = _QUEUED
            row.not_before = not_before
            row.updated_at = now
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "defer_agent_queue_item", write)

    def mark_sent(self, item_id: str, *, owner_user_id: str | None) -> AgentQueueItem | None:
        """Record that a claimed item's send succeeded."""
        now = now_epoch()

        def write(session: Session) -> AgentQueueItem | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, item_id, owner_user_id)
            if row is None or row.state != _SENDING:
                return None
            row.state = _SENT
            row.sent_at = now
            row.updated_at = now
            session.execute(
                update(SqlTaskEvent)
                .where(
                    SqlTaskEvent.workspace_id == current_workspace_id(),
                    SqlTaskEvent.owner_user_id == owner_user_id,
                    SqlTaskEvent.queue_item_id == row.id,
                )
                .values(delivery_count=SqlTaskEvent.delivery_count + 1)
                .execution_options(synchronize_session=False)
            )
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "mark_agent_queue_item_sent", write)

    def mark_done(self, item_id: str, *, owner_user_id: str | None) -> AgentQueueItem | None:
        """Record that the turn that handled a sent item ended."""
        now = now_epoch()

        def write(session: Session) -> AgentQueueItem | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, item_id, owner_user_id)
            if row is None or row.state != _SENT:
                return None
            row.state = _DONE
            row.done_at = now
            row.updated_at = now
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "mark_agent_queue_item_done", write)

    def retry_later(
        self,
        item_id: str,
        *,
        owner_user_id: str | None,
        error: str,
        not_before: int,
    ) -> AgentQueueItem | None:
        """Put a claimed item back in the queue after a failed send."""
        now = now_epoch()

        def write(session: Session) -> AgentQueueItem | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, item_id, owner_user_id)
            if row is None or row.state != _SENDING:
                return None
            row.state = _QUEUED
            row.attempts += 1
            row.not_before = not_before
            row.last_error = error
            row.updated_at = now
            return _to_entity(row)

        return run_write_transaction(
            self._session_immediate, "retry_agent_queue_item_later", write
        )

    def delete(self, item_id: str, *, owner_user_id: str | None) -> bool:
        """Delete the owner's item and release its events, so they can be queued again."""

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, item_id, owner_user_id)
            if row is None:
                return False
            session.execute(
                update(SqlTaskEvent)
                .where(
                    SqlTaskEvent.workspace_id == current_workspace_id(),
                    SqlTaskEvent.owner_user_id == owner_user_id,
                    SqlTaskEvent.queue_item_id == row.id,
                )
                .values(queue_item_id=None)
            )
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "delete_agent_queue_item", write)
