"""SQLAlchemy-backed agent-queue store."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import asc, desc, func, or_, select, update

from omnigent.db.db_models import (
    SqlAgentQueue,
    SqlAgentQueueItem,
    SqlDispatchStop,
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
# queue: waiting, running, and the two parked states. Only "done" and
# "cancelled" release a claim — a parked item is retryable, so re-packaging its
# sources would duplicate the work it is still holding.
_OPEN_ITEM_STATES = ("queued", "dispatched", "dispatch_failed", "interrupted")


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
        inspection_hold_token=row.inspection_hold_token,
        inspection_hold_expires_at=row.inspection_hold_expires_at,
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
        seq=row.seq,
        not_before=row.not_before,
        retry_count=row.retry_count,
        edit_lease_token=row.edit_lease_token,
        edit_lease_expires_at=row.edit_lease_expires_at,
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
                        SqlAgentQueue.inspection_hold_token.is_(None),
                        SqlAgentQueue.inspection_hold_expires_at.is_(None),
                        SqlAgentQueue.inspection_hold_expires_at <= now,
                    )
                )
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
            if (
                row.edit_lease_token is not None
                and row.edit_lease_expires_at is not None
                and row.edit_lease_expires_at > now
            ):
                return None
            # Claiming the in-flight slot is the serialisation point, and it is
            # checked last so a lost race leaves nothing to undo. The claim only
            # succeeds when the slot is empty and no edit lease became active
            # after the row was read.
            active_item_hold = (
                select(SqlAgentQueueItem.id)
                .where(SqlAgentQueueItem.workspace_id == current_workspace_id())
                .where(SqlAgentQueueItem.id == item_id)
                .where(SqlAgentQueueItem.edit_lease_token.is_not(None))
                .where(SqlAgentQueueItem.edit_lease_expires_at.is_not(None))
                .where(SqlAgentQueueItem.edit_lease_expires_at > now)
                .exists()
            )
            claim = (
                update(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.role == key.role)
                .where(SqlAgentQueue.owner_user_id == key.owner_user_id)
                .where(SqlAgentQueue.scope_id == _scope_to_column(key.scope_id))
                .where(SqlAgentQueue.inflight_item_id.is_(None))
                .where(~active_item_hold)
                .where(
                    or_(
                        SqlAgentQueue.inspection_hold_token.is_(None),
                        SqlAgentQueue.inspection_hold_expires_at.is_(None),
                        SqlAgentQueue.inspection_hold_expires_at <= now,
                    )
                )
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
        retryable: bool = False,
        max_retries: int | None = 0,
        backoff_s: int = 0,
    ) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            queue = self._get_queue_row(session, key)
            can_retry = (
                retryable
                # max_retries=None means unlimited: the dispatcher retries a
                # transient failure forever with capped backoff so a restart
                # that brings the runner back heals the queue on its own.
                and (max_retries is None or (max_retries > 0 and row.retry_count < max_retries))
                and queue is not None
            )
            if can_retry:
                # Re-queue the item with a backoff delay; the dispatcher
                # picks it up again via due_queues + next_dispatchable_item.
                row.state = encode_agent_queue_item_state("queued")
                row.last_error = error
                row.retry_count = row.retry_count + 1
                row.not_before = now + backoff_s
                row.dispatched_at = None
                row.completed_at = None
                row.updated_at = now
                queue.state = encode_agent_queue_state("active")
                queue.last_error = error
                queue.inflight_item_id = None
                queue.inflight_since = None
                queue.lease_owner = None
                queue.lease_expires_at = None
                queue.next_due_at = now + backoff_s
                queue.updated_at = now
            else:
                # Non-retryable: re-queue with max backoff so the queue
                # stays active and the dispatcher keeps trying. The only
                # permanent stop is a user-initiated pause.
                backoff = 5 * 60
                row.state = encode_agent_queue_item_state("queued")
                row.last_error = error
                row.retry_count = row.retry_count + 1
                row.not_before = now + backoff
                row.dispatched_at = None
                row.completed_at = None
                row.updated_at = now
                if queue is not None:
                    queue.state = encode_agent_queue_state("active")
                    queue.last_error = error
                    queue.inflight_item_id = None
                    queue.inflight_since = None
                    queue.lease_owner = None
                    queue.lease_expires_at = None
                    queue.next_due_at = now + backoff
                    queue.updated_at = now
            session.flush()
            return _item_to_entity(row)

    def reclaim_stale_inflight(self, *, now: int, max_inflight_s: int) -> list[AgentQueueItem]:
        cutoff = now - max_inflight_s
        with self._session() as session:
            stmt = (
                select(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.inflight_item_id.is_not(None))
                .where(SqlAgentQueue.inflight_since.is_not(None))
                .where(SqlAgentQueue.inflight_since <= cutoff)
            )
            reclaimed: list[AgentQueueItem] = []
            for queue in session.execute(stmt).scalars().all():
                item = session.get(
                    SqlAgentQueueItem,
                    (current_workspace_id(), queue.inflight_item_id),
                )
                # Free the slot either way, so a vanished item cannot wedge the
                # queue forever.
                queue.inflight_item_id = None
                queue.inflight_since = None
                queue.updated_at = now
                if item is None or item.state != encode_agent_queue_item_state("dispatched"):
                    continue
                # Park rather than complete. The agent went away mid-item, so
                # calling this "done" would record unfinished work as finished
                # and leave the user no way to notice or retry.
                backoff = 30
                item.state = encode_agent_queue_item_state("queued")
                item.last_error = "agent went away while the item was in flight"
                item.retry_count = item.retry_count + 1
                item.not_before = now + backoff
                item.dispatched_at = None
                item.completed_at = None
                item.updated_at = now
                queue.state = encode_agent_queue_state("active")
                queue.last_error = item.last_error
                queue.next_due_at = now + backoff
                queue.lease_owner = None
                queue.lease_expires_at = None
                reclaimed.append(_item_to_entity(item))
            session.flush()
            return reclaimed

    def set_queue_conversation(
        self,
        key: AgentQueueKey,
        conversation_id: str,
    ) -> AgentQueue | None:
        with self._session() as session:
            row = self._get_queue_row(session, key)
            if row is None:
                return None
            row.conversation_id = conversation_id
            row.updated_at = now_epoch()
            session.flush()
            return _queue_to_entity(row)

    def complete_inflight_for_session(
        self,
        session_id: str,
        *,
        now: int,
    ) -> AgentQueueItem | None:
        with self._session() as session:
            stmt = (
                select(SqlAgentQueue)
                .where(SqlAgentQueue.workspace_id == current_workspace_id())
                .where(SqlAgentQueue.conversation_id == session_id)
                .where(SqlAgentQueue.inflight_item_id.is_not(None))
                .limit(1)
            )
            queue = session.execute(stmt).scalars().first()
            if queue is None:
                return None
            item_id = queue.inflight_item_id
            queue.inflight_item_id = None
            queue.inflight_since = None
            queue.updated_at = now
            item = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if item is not None and item.state == encode_agent_queue_item_state("dispatched"):
                item.state = encode_agent_queue_item_state("done")
                item.completed_at = now
                item.updated_at = now
            session.flush()
            return _item_to_entity(item) if item is not None else None

    def get_dispatch_stoplist(self) -> frozenset[str]:
        with self._session() as session:
            rows = session.execute(
                select(SqlDispatchStop.role).where(
                    SqlDispatchStop.workspace_id == current_workspace_id()
                )
            ).scalars().all()
            return frozenset(rows)

    def set_role_dispatch_stopped(self, role: str, stopped: bool) -> None:
        now = now_epoch()
        with self._session() as session:
            row = session.get(
                SqlDispatchStop, (current_workspace_id(), role)
            )
            if stopped:
                if row is None:
                    session.add(
                        SqlDispatchStop(
                            role=role, created_at=now, updated_at=now
                        )
                    )
                else:
                    row.updated_at = now
            elif row is not None:
                session.delete(row)
            session.flush()

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

    def acquire_inspection_hold(
        self,
        key: AgentQueueKey,
        token: str,
        *,
        now: int,
        ttl_s: int,
    ) -> AgentQueue:
        scope = _scope_to_column(key.scope_id)
        with self._session() as session:
            row = self._get_queue_row(session, key)
            if row is None:
                row = SqlAgentQueue(
                    role=key.role,
                    owner_user_id=key.owner_user_id,
                    scope_id=scope,
                    state=encode_agent_queue_state("active"),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
            if (
                row.inspection_hold_token is not None
                and row.inspection_hold_token != token
                and row.inspection_hold_expires_at is not None
                and row.inspection_hold_expires_at > now
            ):
                raise ValueError("queue already has an active inspection hold")
            row.inspection_hold_token = token
            row.inspection_hold_expires_at = now + ttl_s
            row.updated_at = now
            session.flush()
            return _queue_to_entity(row)

    def release_inspection_hold(self, key: AgentQueueKey, token: str) -> bool:
        with self._session() as session:
            row = self._get_queue_row(session, key)
            if row is None or row.inspection_hold_token != token:
                return False
            row.inspection_hold_token = None
            row.inspection_hold_expires_at = None
            row.next_due_at = None
            row.updated_at = now_epoch()
            session.flush()
            return True

    def get_item(self, item_id: str) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            return _item_to_entity(row)

    def find_open_item_for_source(
        self, source_id: str, *, role: str | None = None
    ) -> AgentQueueItem | None:
        with self._session() as session:
            stmt = (
                select(SqlAgentQueueItem)
                .where(SqlAgentQueueItem.workspace_id == current_workspace_id())
                .where(SqlAgentQueueItem.state.in_(_open_item_codes()))
                .order_by(desc(SqlAgentQueueItem.seq), desc(SqlAgentQueueItem.id))
            )
            if role is not None:
                stmt = stmt.where(SqlAgentQueueItem.role == role)
            for row in session.execute(stmt).scalars().all():
                if source_id in _decode_source_ids(row.source_ids):
                    return _item_to_entity(row)
            return None

    def list_open_items_for_role(self, role: str) -> list[AgentQueueItem]:
        with self._session() as session:
            stmt = (
                select(SqlAgentQueueItem)
                .where(SqlAgentQueueItem.workspace_id == current_workspace_id())
                .where(SqlAgentQueueItem.role == role)
                .where(SqlAgentQueueItem.state.in_(_open_item_codes()))
                .order_by(SqlAgentQueueItem.seq, SqlAgentQueueItem.id)
            )
            return [_item_to_entity(row) for row in session.execute(stmt).scalars().all()]

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
                asc(SqlAgentQueueItem.seq),
                asc(SqlAgentQueueItem.id),
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [_item_to_entity(row) for row in rows]

    def acquire_item_edit_lease(
        self,
        item_id: str,
        token: str,
        *,
        now: int,
        ttl_s: int,
    ) -> AgentQueueItem | None:
        with self._session() as session:
            stmt = (
                update(SqlAgentQueueItem)
                .where(SqlAgentQueueItem.workspace_id == current_workspace_id())
                .where(SqlAgentQueueItem.id == item_id)
                .where(SqlAgentQueueItem.state == encode_agent_queue_item_state("queued"))
                .where(
                    or_(
                        SqlAgentQueueItem.edit_lease_token.is_(None),
                        SqlAgentQueueItem.edit_lease_token == token,
                        SqlAgentQueueItem.edit_lease_expires_at.is_(None),
                        SqlAgentQueueItem.edit_lease_expires_at <= now,
                    )
                )
                .values(
                    edit_lease_token=token,
                    edit_lease_expires_at=now + ttl_s,
                    updated_at=now,
                )
            )
            if session.execute(stmt).rowcount != 1:
                return None
            session.flush()
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            return _item_to_entity(row) if row is not None else None

    def release_item_edit_lease(self, item_id: str, token: str) -> bool:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None or row.edit_lease_token != token:
                return False
            row.edit_lease_token = None
            row.edit_lease_expires_at = None
            row.updated_at = now_epoch()
            queue = self._get_queue_row(session, _item_to_entity(row).key)
            if queue is not None:
                queue.next_due_at = None
                queue.updated_at = row.updated_at
            session.flush()
            return True

    def update_item(
        self,
        item_id: str,
        *,
        payload: str | None = _UNSET,
        not_before: int | None = _UNSET,
        edit_lease_token: str | None = None,
    ) -> AgentQueueItem | None:
        now = now_epoch()
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            editable_states = (
                encode_agent_queue_item_state("queued"),
                encode_agent_queue_item_state("interrupted"),
                encode_agent_queue_item_state("dispatch_failed"),
            )
            if row.state not in editable_states:
                return None
            if (
                row.state == encode_agent_queue_item_state("queued")
                and edit_lease_token is not None
                and (
                    row.edit_lease_token != edit_lease_token
                    or row.edit_lease_expires_at is None
                    or row.edit_lease_expires_at <= now
                )
            ):
                return None
            if payload is not _UNSET:
                row.payload = payload
            if not_before is not _UNSET:
                row.not_before = not_before
            row.updated_at = now
            session.flush()
            return _item_to_entity(row)

    def retry_parked_item(self, item_id: str, *, now: int) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            parked = (
                encode_agent_queue_item_state("dispatch_failed"),
                encode_agent_queue_item_state("interrupted"),
            )
            if row is None or row.state not in parked:
                return None
            row.state = encode_agent_queue_item_state("queued")
            row.last_error = None
            row.retry_count = 0
            row.completed_at = None
            row.dispatched_at = None
            row.edit_lease_token = None
            row.edit_lease_expires_at = None
            row.updated_at = now
            queue = self._get_queue_row(session, _item_to_entity(row).key)
            if queue is not None:
                queue.state = encode_agent_queue_state("active")
                queue.last_error = None
                queue.next_due_at = None
                queue.lease_owner = None
                queue.lease_expires_at = None
                queue.updated_at = now
            session.flush()
            return _item_to_entity(row)

    def cancel_item(self, item_id: str, *, now: int) -> AgentQueueItem | None:
        with self._session() as session:
            row = session.get(SqlAgentQueueItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            queued = encode_agent_queue_item_state("queued")
            parked = (
                encode_agent_queue_item_state("dispatch_failed"),
                encode_agent_queue_item_state("interrupted"),
            )
            cancelled = encode_agent_queue_item_state("cancelled")
            # Already cancelled: idempotent no-op. Dispatched/done items are
            # not cancelable — the agent already has them or they finished.
            if row.state == cancelled:
                return _item_to_entity(row)
            if row.state != queued and row.state not in parked:
                return None
            was_parked = row.state in parked
            row.state = cancelled
            row.completed_at = now
            row.updated_at = now
            # A parked item halts the queue and is itself the blockage.
            # Cancelling it is the complete recovery: clear the halt in the same
            # call so the slot can accept new work, rather than making the user
            # also find and press resume.
            if was_parked:
                key = AgentQueueKey(
                    role=row.role,
                    owner_user_id=row.owner_user_id,
                    scope_id=row.scope_id if row.scope_id else None,
                )
                queue = self._get_queue_row(session, key)
                # Queue is always active now (no halted state); nothing to clear.
            session.flush()
            return _item_to_entity(row)

    def purge_old_items(self, *, before_ts: int, states: list[str]) -> int:
        from sqlalchemy import delete

        encoded_states = [encode_agent_queue_item_state(s) for s in states]
        with self._session() as session:
            stmt = (
                delete(SqlAgentQueueItem)
                .where(SqlAgentQueueItem.workspace_id == current_workspace_id())
                .where(SqlAgentQueueItem.created_at < before_ts)
                .where(SqlAgentQueueItem.state.in_(encoded_states))
            )
            result = session.execute(stmt)
            session.flush()
            return result.rowcount or 0

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
