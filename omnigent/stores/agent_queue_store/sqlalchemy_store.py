"""SQLAlchemy-backed agent-queue store."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import asc, desc, func, or_, select, update

from omnigent.db.db_models import (
    SqlAgentQueue,
    SqlAgentQueueItem,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_agent_queue_item_state,
    decode_agent_queue_state,
    encode_agent_queue_item_state,
    encode_agent_queue_state,
)
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import AgentQueue, AgentQueueItem, AgentQueueKey
from omnigent.stores.agent_queue_store import AgentQueueStore

_logger = logging.getLogger(__name__)

_UNSET: Any = object()

# Item states that still hold a claim on their source ids and still occupy the
# queue. Anything else has left the queue for good.
_OPEN_ITEM_STATES = ("queued", "dispatched")


def _scope_to_column(scope_id: str | None) -> str:
    """Map a nullable scope to its non-NULL key-column form."""
    return scope_id or ""


def _scope_from_column(value: str) -> str | None:
    """Map a key-column scope back to its nullable entity form."""
    return value or None


def _queue_to_entity(row: SqlAgentQueue) -> AgentQueue:
    return AgentQueue(
        role=row.role,
        owner_user_id=row.owner_user_id,
        scope_id=_scope_from_column(row.scope_id),
        state=decode_agent_queue_state(row.state),
        created_at=row.created_at,
        conversation_id=row.conversation_id,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        next_due_at=row.next_due_at,
        inflight_item_id=row.inflight_item_id,
        inflight_since=row.inflight_since,
        last_error=row.last_error,
        updated_at=row.updated_at,
    )


def _item_to_entity(row: SqlAgentQueueItem) -> AgentQueueItem:
    return AgentQueueItem(
        id=row.id,
        role=row.role,
        owner_user_id=row.owner_user_id,
        scope_id=_scope_from_column(row.scope_id),
        kind=row.kind,
        state=decode_agent_queue_item_state(row.state),
        created_at=row.created_at,
        source_ids=_decode_source_ids(row.source_ids),
        payload=row.payload,
        priority=row.priority,
        seq=row.seq,
        not_before=row.not_before,
        last_error=row.last_error,
        updated_at=row.updated_at,
        dispatched_at=row.dispatched_at,
        completed_at=row.completed_at,
    )


def _decode_source_ids(raw: str | None) -> list[str]:
    """Decode the stored JSON list, tolerating a malformed value."""
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except ValueError:
        _logger.warning("discarding malformed agent_queue_items.source_ids: %r", raw)
        return []
    if not isinstance(decoded, list):
        return []
    return [str(value) for value in decoded]


class SqlAlchemyAgentQueueStore(AgentQueueStore):
    """SQLAlchemy-backed implementation of :class:`AgentQueueStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    # ── Producers ──────────────────────────────────────

    def enqueue(
        self,
        item_id: str,
        key: AgentQueueKey,
        kind: str,
        *,
        source_ids: list[str] | None = None,
        payload: str | None = None,
        priority: int = 0,
        not_before: int | None = None,
    ) -> AgentQueueItem:
        now = now_epoch()
        scope = _scope_to_column(key.scope_id)
        with self._session() as session:
            # Arrival order, following the same max()+1 pattern as task_assets.
            # A concurrent enqueue could in principle duplicate a sequence; the
            # cost is two same-second items ordering by id instead, never a lost
            # or duplicated item, so this does not need a heavier lock.
            next_seq = session.scalar(
                select(func.coalesce(func.max(SqlAgentQueueItem.seq), 0) + 1).where(
                    SqlAgentQueueItem.workspace_id == current_workspace_id(),
                ),
            )
            row = SqlAgentQueueItem(
                id=item_id,
                role=key.role,
                owner_user_id=key.owner_user_id,
                scope_id=scope,
                kind=kind,
                source_ids=json.dumps(list(source_ids or [])),
                payload=payload,
                state=encode_agent_queue_item_state("queued"),
                priority=priority,
                seq=next_seq or 1,
                not_before=not_before,
                created_at=now,
                updated_at=None,
            )
            existing = session.get(
                SqlAgentQueue,
                (current_workspace_id(), key.role, key.owner_user_id, scope),
            )
            if existing is None:
                session.add(
                    SqlAgentQueue(
                        role=key.role,
                        owner_user_id=key.owner_user_id,
                        scope_id=scope,
                        state=encode_agent_queue_state("active"),
                        created_at=now,
                        updated_at=None,
                    )
                )
            session.add(row)
            session.flush()
            return _item_to_entity(row)

    def list_claimed_source_ids(
        self,
        role: str,
        owner_user_id: str,
        *,
        scope_id: str | None = None,
    ) -> set[str]:
        with self._session() as session:
            stmt = (
                select(SqlAgentQueueItem.source_ids)
                .where(SqlAgentQueueItem.workspace_id == current_workspace_id())
                .where(SqlAgentQueueItem.role == role)
                .where(SqlAgentQueueItem.owner_user_id == owner_user_id)
                .where(SqlAgentQueueItem.state.in_(_open_item_codes()))
            )
            if scope_id is not None:
                stmt = stmt.where(SqlAgentQueueItem.scope_id == _scope_to_column(scope_id))
            claimed: set[str] = set()
            for raw in session.execute(stmt).scalars().all():
                claimed.update(_decode_source_ids(raw))
            return claimed

    # ── Dispatcher ─────────────────────────────────────

    def due_queues(self, *, now: int, limit: int = 100) -> list[AgentQueue]:
        with self._session() as session:
            queued = encode_agent_queue_item_state("queued")
            has_work = (
                select(SqlAgentQueueItem.id)
                .where(SqlAgentQueueItem.workspace_id == SqlAgentQueue.workspace_id)
                .where(SqlAgentQueueItem.role == SqlAgentQueue.role)
                .where(SqlAgentQueueItem.owner_user_id == SqlAgentQueue.owner_user_id)
                .where(SqlAgentQueueItem.scope_id == SqlAgentQueue.scope_id)
                .where(SqlAgentQueueItem.state == queued)
                .where(
                    or_(
                        SqlAgentQueueItem.not_before.is_(None),
                        SqlAgentQueueItem.not_before <= now,
                    )
                )
                .exists()
            )
            stmt = (
                select(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.state == encode_agent_queue_state("active"))
                .where(SqlAgentQueue.inflight_item_id.is_(None))
                .where(
                    or_(
                        SqlAgentQueue.next_due_at.is_(None),
                        SqlAgentQueue.next_due_at <= now,
                    )
                )
                .where(
                    or_(
                        SqlAgentQueue.lease_expires_at.is_(None),
                        SqlAgentQueue.lease_expires_at <= now,
                    )
                )
                .where(has_work)
                .order_by(asc(SqlAgentQueue.next_due_at), asc(SqlAgentQueue.created_at))
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
            return [_queue_to_entity(row) for row in rows]

    def acquire_lease(
        self,
        key: AgentQueueKey,
        lease_owner: str,
        *,
        now: int,
        ttl_s: int,
    ) -> AgentQueue | None:
        with self._session() as session:
            # Conditional update rather than read-then-write: two dispatchers
            # racing for the same queue both see it free on read, but only one
            # UPDATE matches the "unleased or expired" predicate.
            stmt = (
                update(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.role == key.role)
                .where(SqlAgentQueue.owner_user_id == key.owner_user_id)
                .where(SqlAgentQueue.scope_id == _scope_to_column(key.scope_id))
                .where(SqlAgentQueue.state == encode_agent_queue_state("active"))
                .where(
                    or_(
                        SqlAgentQueue.lease_owner.is_(None),
                        SqlAgentQueue.lease_expires_at.is_(None),
                        SqlAgentQueue.lease_expires_at <= now,
                    )
                )
                .values(
                    lease_owner=lease_owner,
                    lease_expires_at=now + ttl_s,
                    updated_at=now,
                )
            )
            if session.execute(stmt).rowcount != 1:
                return None
            session.flush()
            return self._read_queue(session, key)

    def renew_lease(
        self,
        key: AgentQueueKey,
        lease_owner: str,
        *,
        now: int,
        ttl_s: int,
    ) -> bool:
        with self._session() as session:
            stmt = (
                update(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.role == key.role)
                .where(SqlAgentQueue.owner_user_id == key.owner_user_id)
                .where(SqlAgentQueue.scope_id == _scope_to_column(key.scope_id))
                .where(SqlAgentQueue.lease_owner == lease_owner)
                .values(lease_expires_at=now + ttl_s, updated_at=now)
            )
            return session.execute(stmt).rowcount == 1

    def release_lease(
        self,
        key: AgentQueueKey,
        lease_owner: str,
        *,
        next_due_at: int | None = None,
    ) -> None:
        with self._session() as session:
            stmt = (
                update(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.role == key.role)
                .where(SqlAgentQueue.owner_user_id == key.owner_user_id)
                .where(SqlAgentQueue.scope_id == _scope_to_column(key.scope_id))
                .where(SqlAgentQueue.lease_owner == lease_owner)
                .values(
                    lease_owner=None,
                    lease_expires_at=None,
                    next_due_at=next_due_at,
                    updated_at=now_epoch(),
                )
            )
            session.execute(stmt)

    def next_dispatchable_item(
        self,
        key: AgentQueueKey,
        *,
        now: int,
    ) -> AgentQueueItem | None:
        with self._session() as session:
            stmt = (
                _item_query(key)
                .where(SqlAgentQueueItem.state == encode_agent_queue_item_state("queued"))
                .where(
                    or_(
                        SqlAgentQueueItem.not_before.is_(None),
                        SqlAgentQueueItem.not_before <= now,
                    )
                )
                .order_by(
                    desc(SqlAgentQueueItem.priority),
                    asc(SqlAgentQueueItem.seq),
                    asc(SqlAgentQueueItem.id),
                )
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _item_to_entity(row)

    def mark_dispatched(
        self,
        item_id: str,
        key: AgentQueueKey,
        *,
        now: int,
    ) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None or row.state != encode_agent_queue_item_state("queued"):
                return None
            # Claiming the in-flight slot is the serialisation point, and it is
            # checked last so a lost race leaves nothing to undo. The claim only
            # succeeds when the slot is empty, so a second dispatcher holding the
            # same head item cannot also send it.
            claim = (
                update(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.role == key.role)
                .where(SqlAgentQueue.owner_user_id == key.owner_user_id)
                .where(SqlAgentQueue.scope_id == _scope_to_column(key.scope_id))
                .where(SqlAgentQueue.inflight_item_id.is_(None))
                .values(inflight_item_id=item_id, inflight_since=now, updated_at=now)
            )
            if session.execute(claim).rowcount != 1:
                return None
            row.state = encode_agent_queue_item_state("dispatched")
            row.dispatched_at = now
            row.updated_at = now
            session.flush()
            return _item_to_entity(row)

    def complete_inflight(
        self,
        key: AgentQueueKey,
        *,
        item_id: str | None = None,
        now: int,
    ) -> AgentQueueItem | None:
        with self._session() as session:
            queue = self._get_queue_row(session, key)
            if queue is None or queue.inflight_item_id is None:
                return None
            if item_id is not None and queue.inflight_item_id != item_id:
                return None
            completed_id = queue.inflight_item_id
            queue.inflight_item_id = None
            queue.inflight_since = None
            queue.updated_at = now
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), completed_id))
            if row is None:
                session.flush()
                return None
            if row.state == encode_agent_queue_item_state("dispatched"):
                row.state = encode_agent_queue_item_state("done")
                row.completed_at = now
                row.updated_at = now
            session.flush()
            return _item_to_entity(row)

    def fail_dispatch(
        self,
        item_id: str,
        key: AgentQueueKey,
        *,
        error: str,
        now: int,
    ) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            row.state = encode_agent_queue_item_state("dispatch_failed")
            row.last_error = error
            row.completed_at = now
            row.updated_at = now
            queue = self._get_queue_row(session, key)
            if queue is not None:
                queue.state = encode_agent_queue_state("halted")
                queue.last_error = error
                queue.inflight_item_id = None
                queue.inflight_since = None
                queue.lease_owner = None
                queue.lease_expires_at = None
                queue.updated_at = now
            session.flush()
            return _item_to_entity(row)

    def reclaim_stale_inflight(self, *, now: int, max_inflight_s: int) -> list[AgentQueue]:
        cutoff = now - max_inflight_s
        with self._session() as session:
            stmt = (
                select(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.inflight_item_id.is_not(None))
                .where(SqlAgentQueue.inflight_since.is_not(None))
                .where(SqlAgentQueue.inflight_since <= cutoff)
            )
            reclaimed: list[AgentQueue] = []
            for queue in session.execute(stmt).scalars().all():
                item = session.get(
                    SqlAgentQueueItem,
                    (current_workspace_id(), queue.inflight_item_id),
                )
                if item is not None and item.state == encode_agent_queue_item_state("dispatched"):
                    item.state = encode_agent_queue_item_state("done")
                    item.completed_at = now
                    item.updated_at = now
                queue.inflight_item_id = None
                queue.inflight_since = None
                queue.updated_at = now
                reclaimed.append(_queue_to_entity(queue))
            session.flush()
            return reclaimed

    # ── Control plane ──────────────────────────────────

    def get_queue(self, key: AgentQueueKey) -> AgentQueue | None:
        with self._session() as session:
            return self._read_queue(session, key)

    def list_queues(
        self,
        *,
        role: str | None = None,
        owner_user_id: str | None = None,
        state: str | None = None,
    ) -> list[AgentQueue]:
        with self._session() as session:
            stmt = select(SqlAgentQueue).where(
                SqlAgentQueue.workspace_id == current_workspace_id(),
            )
            if role is not None:
                stmt = stmt.where(SqlAgentQueue.role == role)
            if owner_user_id is not None:
                stmt = stmt.where(SqlAgentQueue.owner_user_id == owner_user_id)
            if state is not None:
                stmt = stmt.where(SqlAgentQueue.state == encode_agent_queue_state(state))
            stmt = stmt.order_by(
                desc(SqlAgentQueue.updated_at),
                desc(SqlAgentQueue.created_at),
                asc(SqlAgentQueue.role),
                asc(SqlAgentQueue.scope_id),
            )
            rows = session.execute(stmt).scalars().all()
            return [_queue_to_entity(row) for row in rows]

    def queue_depth(self, key: AgentQueueKey) -> int:
        with self._session() as session:
            stmt = _item_query(key).where(
                SqlAgentQueueItem.state == encode_agent_queue_item_state("queued"),
            )
            return len(session.execute(stmt).scalars().all())

    def set_queue_state(
        self,
        key: AgentQueueKey,
        state: str,
        *,
        last_error: str | None = _UNSET,
    ) -> AgentQueue | None:
        with self._session() as session:
            row = self._get_queue_row(session, key)
            if row is None:
                return None
            row.state = encode_agent_queue_state(state)
            if last_error is not _UNSET:
                row.last_error = last_error
            elif state == "active":
                row.last_error = None
            if state == "active":
                # Re-arm immediately: a resume is an explicit "try again now".
                row.next_due_at = None
                row.lease_owner = None
                row.lease_expires_at = None
            row.updated_at = now_epoch()
            session.flush()
            return _queue_to_entity(row)

    def get_item(self, item_id: str) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            return _item_to_entity(row)

    def list_items(
        self,
        key: AgentQueueKey,
        *,
        state: str | None = None,
        limit: int | None = None,
    ) -> list[AgentQueueItem]:
        with self._session() as session:
            stmt = _item_query(key)
            if state is not None:
                stmt = stmt.where(
                    SqlAgentQueueItem.state == encode_agent_queue_item_state(state),
                )
            stmt = stmt.order_by(
                desc(SqlAgentQueueItem.priority),
                asc(SqlAgentQueueItem.seq),
                asc(SqlAgentQueueItem.id),
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [_item_to_entity(row) for row in rows]

    def update_item(
        self,
        item_id: str,
        *,
        payload: str | None = _UNSET,
        priority: int | None = None,
        not_before: int | None = _UNSET,
    ) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            if row.state != encode_agent_queue_item_state("queued"):
                return None
            if payload is not _UNSET:
                row.payload = payload
            if priority is not None:
                row.priority = priority
            if not_before is not _UNSET:
                row.not_before = not_before
            row.updated_at = now_epoch()
            session.flush()
            return _item_to_entity(row)

    def cancel_item(self, item_id: str, *, now: int) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            if row.state != encode_agent_queue_item_state("queued"):
                return None
            row.state = encode_agent_queue_item_state("cancelled")
            row.completed_at = now
            row.updated_at = now
            session.flush()
            return _item_to_entity(row)

    # ── Internals ──────────────────────────────────────

    def _get_queue_row(self, session: Any, key: AgentQueueKey) -> SqlAgentQueue | None:
        return session.get(
            SqlAgentQueue,
            (
                current_workspace_id(),
                key.role,
                key.owner_user_id,
                _scope_to_column(key.scope_id),
            ),
        )

    def _read_queue(self, session: Any, key: AgentQueueKey) -> AgentQueue | None:
        row = self._get_queue_row(session, key)
        if row is None:
            return None
        return _queue_to_entity(row)


def _open_item_codes() -> list[int]:
    """Return the int codes of item states that still occupy the queue."""
    return [encode_agent_queue_item_state(name) for name in _OPEN_ITEM_STATES]


def _item_query(key: AgentQueueKey) -> Any:
    """Return a SELECT narrowed to one queue's items."""
    return (
        select(SqlAgentQueueItem)
        .where(SqlAgentQueueItem.workspace_id == current_workspace_id())
        .where(SqlAgentQueueItem.role == key.role)
        .where(SqlAgentQueueItem.owner_user_id == key.owner_user_id)
        .where(SqlAgentQueueItem.scope_id == _scope_to_column(key.scope_id))
    )
