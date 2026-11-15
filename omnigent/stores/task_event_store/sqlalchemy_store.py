"""SQLAlchemy-backed GlobalHub task event store."""

from __future__ import annotations

import builtins
import json
from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import ColumnElement, and_, asc, or_, select
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlAgentQueueItem, SqlTaskEvent, current_workspace_id
from omnigent.db.enum_codecs import (
    decode_task_event_state,
    encode_agent_queue_item_state,
    encode_task_event_state,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import EventTag, TaskEvent
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.util.uuid import normalize_uuid

_UNROUTED = encode_task_event_state("unrouted")
_ROUTED = encode_task_event_state("routed")
_DISMISSED = encode_task_event_state("dismissed")
_DONE_ITEM = encode_agent_queue_item_state("done")


def _to_entity(row: SqlTaskEvent) -> TaskEvent:
    return TaskEvent(
        id=row.id,
        owner_user_id=row.owner_user_id,
        event_type=row.event_type,
        title=row.title,
        state=decode_task_event_state(row.state),
        created_at=row.created_at,
        payload=json.loads(row.payload) if row.payload else {},
        source=row.source,
        source_key=row.source_key,
        source_offset=row.source_offset,
        source_session_id=row.source_session_id,
        tags=[EventTag(tag_type=tag["tag_type"], tag=tag["tag"]) for tag in json.loads(row.tags)],
        task_id=row.task_id,
        manager_id=row.manager_id,
        fyi_cluster_id=row.fyi_cluster_id,
        queue_item_id=row.queue_item_id,
        delivery_count=row.delivery_count,
        updated_at=row.updated_at,
        routed_at=row.routed_at,
        processed_at=row.processed_at,
        workspace_id=row.workspace_id,
    )


def new_task_event_row(
    event_id: str,
    *,
    owner_user_id: str | None,
    event_type: str,
    title: str,
    now: int,
    payload: dict[str, Any] | None = None,
    source: str | None = None,
    source_key: str | None = None,
    source_offset: str | None = None,
    source_session_id: str | None = None,
    tags: Sequence[EventTag] = (),
    task_id: str | None = None,
    manager_id: str | None = None,
) -> SqlTaskEvent:
    """
    Build a task event row: routed when ``manager_id`` is given, else unrouted.

    Another store adds the row to its own transaction, e.g. to record an event
    together with the change it reports.
    """
    routed = manager_id is not None
    return SqlTaskEvent(
        id=event_id,
        owner_user_id=owner_user_id,
        event_type=event_type,
        title=title,
        payload=json.dumps(payload, ensure_ascii=False) if payload else None,
        source=source,
        source_key=source_key,
        source_offset=source_offset,
        source_session_id=source_session_id,
        tags=json.dumps(
            [{"tag_type": tag.tag_type, "tag": tag.tag} for tag in tags],
            ensure_ascii=False,
        ),
        state=_ROUTED if routed else _UNROUTED,
        task_id=task_id,
        manager_id=manager_id,
        delivery_count=0,
        created_at=now,
        updated_at=None,
        routed_at=now if routed else None,
        processed_at=None,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlTaskEvent.workspace_id == current_workspace_id(),
        SqlTaskEvent.owner_user_id == owner_user_id,
    )


def _owned_row(session: Session, event_id: str, owner_user_id: str | None) -> SqlTaskEvent | None:
    row = session.get(SqlTaskEvent, (current_workspace_id(), event_id))
    return row if row is not None and row.owner_user_id == owner_user_id else None


class SqlAlchemyTaskEventStore(TaskEventStore):
    """
    SQLAlchemy-backed implementation of :class:`TaskEventStore`.

    Every query is scoped by ``workspace_id`` and ``owner_user_id``.
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
            query_name_prefix="omnigent.task_event_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.task_event_store",
            immediate=True,
        )

    def create(
        self,
        event_id: str,
        *,
        owner_user_id: str | None,
        event_type: str,
        title: str,
        payload: dict[str, Any] | None = None,
        source: str | None = None,
        source_key: str | None = None,
        source_offset: str | None = None,
        source_session_id: str | None = None,
        tags: Sequence[EventTag] = (),
        task_id: str | None = None,
        manager_id: str | None = None,
    ) -> TaskEvent:
        """Record an event: routed when ``manager_id`` is given, else unrouted."""
        now = now_epoch()

        def write(session: Session) -> TaskEvent:
            require_active_account(session, owner_user_id)
            row = new_task_event_row(
                event_id,
                owner_user_id=owner_user_id,
                event_type=event_type,
                title=title,
                now=now,
                payload=payload,
                source=source,
                source_key=source_key,
                source_offset=source_offset,
                source_session_id=source_session_id,
                tags=tags,
                task_id=task_id,
                manager_id=manager_id,
            )
            session.add(row)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "insert_task_event", write)

    def get(self, event_id: str, *, owner_user_id: str | None) -> TaskEvent | None:
        """Return the owner's event, or ``None`` when missing or someone else's."""
        with self._session("select_task_event_by_id") as session:
            row = _owned_row(session, event_id, owner_user_id)
            return _to_entity(row) if row is not None else None

    def get_by_source(
        self,
        *,
        owner_user_id: str | None,
        event_type: str,
        source: str | None,
        source_key: str | None,
        source_offset: str | None,
    ) -> TaskEvent | None:
        """Return the owner's first event for the same report from a source, or ``None``."""
        stmt = (
            select(SqlTaskEvent)
            .where(
                _owned(owner_user_id),
                SqlTaskEvent.source_key == source_key,
                SqlTaskEvent.source == source,
                SqlTaskEvent.source_offset == source_offset,
                SqlTaskEvent.event_type == event_type,
            )
            .order_by(asc(SqlTaskEvent.created_at), asc(SqlTaskEvent.id))
            .limit(1)
        )
        with self._session("select_task_event_by_source") as session:
            row = session.execute(stmt).scalars().first()
            return _to_entity(row) if row is not None else None

    def list(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        manager_id: str | None = None,
        fyi_cluster_ids: Collection[str] | None = None,
        queue_item_ids: Collection[str] | None = None,
        event_type: str | None = None,
        source_key: str | None = None,
        limit: int | None = None,
    ) -> builtins.list[TaskEvent]:
        """List the owner's events, oldest first."""
        stmt = select(SqlTaskEvent).where(_owned(owner_user_id))
        if states is not None:
            stmt = stmt.where(
                SqlTaskEvent.state.in_([encode_task_event_state(state) for state in states])
            )
        if manager_id is not None:
            stmt = stmt.where(SqlTaskEvent.manager_id == manager_id)
        if fyi_cluster_ids is not None:
            stmt = stmt.where(SqlTaskEvent.fyi_cluster_id.in_(fyi_cluster_ids))
        if queue_item_ids is not None:
            stmt = stmt.where(SqlTaskEvent.queue_item_id.in_(queue_item_ids))
        if event_type is not None:
            stmt = stmt.where(SqlTaskEvent.event_type == event_type)
        if source_key is not None:
            stmt = stmt.where(SqlTaskEvent.source_key == source_key)
        stmt = stmt.order_by(asc(SqlTaskEvent.created_at), asc(SqlTaskEvent.id))
        if limit is not None:
            stmt = stmt.limit(limit)
        with self._session("list_task_events") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def list_waiting_all_workspaces(
        self, *, state: str, max_deliveries: int
    ) -> builtins.list[TaskEvent]:
        """List the events in ``state`` in every workspace that wait to be queued, oldest first."""
        done_item = select(SqlAgentQueueItem.id).where(
            SqlAgentQueueItem.workspace_id == SqlTaskEvent.workspace_id,
            SqlAgentQueueItem.id == SqlTaskEvent.queue_item_id,
            SqlAgentQueueItem.state == _DONE_ITEM,
        )
        stmt = (
            select(SqlTaskEvent)
            .where(
                SqlTaskEvent.state == encode_task_event_state(state),
                SqlTaskEvent.delivery_count < max_deliveries,
                or_(SqlTaskEvent.queue_item_id.is_(None), done_item.exists()),
            )
            .order_by(asc(SqlTaskEvent.created_at), asc(SqlTaskEvent.id))
        )
        with self._session("list_task_events_waiting_for_queue_in_all_workspaces") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def route_to_manager(
        self, event_ids: Collection[str], *, owner_user_id: str | None, manager_id: str
    ) -> builtins.list[TaskEvent] | None:
        """Route the owner's events to one manager, all or none."""
        wanted = list(
            dict.fromkeys(normalize_uuid(event_id) or event_id for event_id in event_ids)
        )
        now = now_epoch()

        def write(session: Session) -> builtins.list[TaskEvent] | None:
            require_active_account(session, owner_user_id)
            stmt = select(SqlTaskEvent).where(_owned(owner_user_id), SqlTaskEvent.id.in_(wanted))
            rows = {row.id: row for row in session.execute(stmt).scalars()}
            if len(rows) != len(wanted):
                return None
            for row in rows.values():
                routed_here = row.state == _ROUTED and row.manager_id == manager_id
                if row.state != _UNROUTED and not routed_here:
                    return None
            for row in rows.values():
                if row.state == _UNROUTED:
                    row.state = _ROUTED
                    row.manager_id = manager_id
                    # The manager's own queue item carries it from here.
                    row.queue_item_id = None
                    row.delivery_count = 0
                    row.routed_at = now
                    row.updated_at = now
            return [_to_entity(rows[event_id]) for event_id in wanted]

        return run_write_transaction(
            self._session_immediate, "route_task_events_to_manager", write
        )

    def dismiss(self, event_id: str, *, owner_user_id: str | None) -> TaskEvent | None:
        """Mark the owner's event dismissed: handled, or noise."""
        now = now_epoch()

        def write(session: Session) -> TaskEvent | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, event_id, owner_user_id)
            if row is None:
                return None
            if row.state != _DISMISSED:
                row.state = _DISMISSED
                row.processed_at = row.processed_at or now
                row.updated_at = now
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "dismiss_task_event", write)
