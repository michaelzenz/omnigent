"""SQLAlchemy-backed task-event store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc, select

from omnigent.db.db_models import (
    SqlTaskEvent,
    SqlTaskEventExecution,
    SqlTaskEventRoutingAttempt,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_task_event_execution_status,
    decode_task_event_state,
    encode_task_event_execution_status,
    encode_task_event_state,
)
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import (
    EventTag,
    TaskEvent,
    TaskEventExecution,
    TaskEventRoutingAttempt,
)
from omnigent.stores.agent_task.tags import decode_event_tags, encode_event_tags
from omnigent.stores.task_event_store import TaskEventStore

_UNSET: Any = object()


def _event_to_entity(row: SqlTaskEvent) -> TaskEvent:
    return TaskEvent(
        id=row.id,
        event_type=row.event_type,
        title=row.title,
        state=decode_task_event_state(row.state),
        created_at=row.created_at,
        tags=decode_event_tags(row.tags),
        task_id=row.task_id,
        payload=row.payload,
        source=row.source,
        source_key=row.source_key,
        source_offset=row.source_offset,
        source_internal_session_id=row.source_internal_session_id,
        owner_user_id=row.owner_user_id,
        updated_at=row.updated_at,
        routed_at=row.routed_at,
        processed_at=row.processed_at,
    )


def _attempt_to_entity(row: SqlTaskEventRoutingAttempt) -> TaskEventRoutingAttempt:
    return TaskEventRoutingAttempt(
        id=row.id,
        event_id=row.event_id,
        candidate_task_id=row.candidate_task_id,
        proposed_at=row.proposed_at,
        score=row.score,
        reason=row.reason,
    )


def _execution_to_entity(row: SqlTaskEventExecution) -> TaskEventExecution:
    return TaskEventExecution(
        id=row.id,
        task_item_id=row.task_item_id,
        task_id=row.task_id,
        agent_queue_item_id=row.agent_queue_item_id,
        status=decode_task_event_execution_status(row.status),
        attempt_no=row.attempt_no,
        assigned_at=row.assigned_at,
        created_at=row.created_at,
        conversation_id=row.conversation_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        result_summary=row.result_summary,
        error=row.error,
        error_code=row.error_code,
        updated_at=row.updated_at,
    )


class SqlAlchemyTaskEventStore(TaskEventStore):
    """SQLAlchemy-backed implementation of :class:`TaskEventStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    def create_event(
        self,
        event_id: str,
        event_type: str,
        title: str,
        *,
        task_id: str | None = None,
        payload: str | None = None,
        source: str | None = None,
        source_key: str | None = None,
        source_offset: int | None = None,
        source_internal_session_id: str | None = None,
        state: str = "received",
        tags: list[EventTag] | None = None,
        owner_user_id: str | None = None,
    ) -> TaskEvent:
        row = SqlTaskEvent(
            id=event_id,
            task_id=task_id,
            event_type=event_type,
            title=title,
            payload=payload,
            source=source,
            source_key=source_key,
            source_offset=source_offset,
            source_internal_session_id=source_internal_session_id,
            owner_user_id=owner_user_id,
            tags=encode_event_tags(tags or []),
            state=encode_task_event_state(state),
            created_at=now_epoch(),
            updated_at=None,
            routed_at=None,
            processed_at=None,
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _event_to_entity(row)

    def get_event(self, event_id: str) -> TaskEvent | None:
        with self._session() as session:
            row = session.get(SqlTaskEvent, (current_workspace_id(), event_id))
            if row is None:
                return None
            return _event_to_entity(row)

    def get_events(self, event_ids: list[str]) -> list[TaskEvent]:
        if not event_ids:
            return []
        with self._session() as session:
            stmt = (
                select(SqlTaskEvent)
                .where(SqlTaskEvent.workspace_id == current_workspace_id())
                .where(SqlTaskEvent.id.in_(event_ids))
            )
            rows = session.execute(stmt).scalars().all()
            return [_event_to_entity(row) for row in rows]

    def get_event_by_source(
        self,
        *,
        source: str,
        source_key: str,
        source_offset: int,
        event_type: str,
    ) -> TaskEvent | None:
        with self._session() as session:
            stmt = (
                select(SqlTaskEvent)
                .where(SqlTaskEvent.workspace_id == current_workspace_id())
                .where(SqlTaskEvent.source == source)
                .where(SqlTaskEvent.source_key == source_key)
                .where(SqlTaskEvent.source_offset == source_offset)
                .where(SqlTaskEvent.event_type == event_type)
                .order_by(desc(SqlTaskEvent.created_at), desc(SqlTaskEvent.id))
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _event_to_entity(row)

    def list_events(
        self,
        *,
        state: str | None = None,
        task_id: str | None = None,
        event_type: str | None = None,
    ) -> list[TaskEvent]:
        with self._session() as session:
            stmt = select(SqlTaskEvent).where(SqlTaskEvent.workspace_id == current_workspace_id())
            if state is not None:
                stmt = stmt.where(SqlTaskEvent.state == encode_task_event_state(state))
            if task_id is not None:
                stmt = stmt.where(SqlTaskEvent.task_id == task_id)
            if event_type is not None:
                stmt = stmt.where(SqlTaskEvent.event_type == event_type)
            stmt = stmt.order_by(desc(SqlTaskEvent.created_at), desc(SqlTaskEvent.id))
            rows = session.execute(stmt).scalars().all()
            return [_event_to_entity(row) for row in rows]

    def update_event(
        self,
        event_id: str,
        *,
        task_id: str | None = _UNSET,
        state: str | None = None,
        routed_at: int | None = None,
        processed_at: int | None = None,
        owner_user_id: str | None = _UNSET,
    ) -> TaskEvent | None:
        with self._session() as session:
            row = session.get(SqlTaskEvent, (current_workspace_id(), event_id))
            if row is None:
                return None
            changed = False
            if task_id is not _UNSET and row.task_id != task_id:
                row.task_id = task_id
                changed = True
            if owner_user_id is not _UNSET and row.owner_user_id != owner_user_id:
                row.owner_user_id = owner_user_id
                changed = True
            if state is not None:
                encoded_state = encode_task_event_state(state)
                if row.state != encoded_state:
                    row.state = encoded_state
                    changed = True
            if routed_at is not None and row.routed_at != routed_at:
                row.routed_at = routed_at
                changed = True
            if processed_at is not None and row.processed_at != processed_at:
                row.processed_at = processed_at
                changed = True
            if changed:
                row.updated_at = now_epoch()
            session.flush()
            return _event_to_entity(row)

    def get_event_tags(self, event_id: str) -> list[EventTag]:
        event = self.get_event(event_id)
        if event is None:
            return []
        return list(event.tags or [])

    def create_routing_attempt(
        self,
        attempt_id: str,
        event_id: str,
        candidate_task_id: str,
        *,
        score: float | None = None,
        reason: str | None = None,
        proposed_at: int | None = None,
    ) -> TaskEventRoutingAttempt:
        row = SqlTaskEventRoutingAttempt(
            id=attempt_id,
            event_id=event_id,
            candidate_task_id=candidate_task_id,
            score=score,
            reason=reason,
            proposed_at=proposed_at if proposed_at is not None else now_epoch(),
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _attempt_to_entity(row)

    def list_routing_attempts(self, event_id: str) -> list[TaskEventRoutingAttempt]:
        with self._session() as session:
            stmt = (
                select(SqlTaskEventRoutingAttempt)
                .where(SqlTaskEventRoutingAttempt.workspace_id == current_workspace_id())
                .where(SqlTaskEventRoutingAttempt.event_id == event_id)
                .order_by(
                    asc(SqlTaskEventRoutingAttempt.proposed_at),
                    asc(SqlTaskEventRoutingAttempt.id),
                )
            )
            rows = session.execute(stmt).scalars().all()
            return [_attempt_to_entity(row) for row in rows]

    def create_execution(
        self,
        execution_id: str,
        task_item_id: str,
        task_id: str,
        *,
        status: str = "queued",
        attempt_no: int = 1,
        agent_queue_item_id: str | None = None,
        conversation_id: str | None = None,
        assigned_at: int | None = None,
    ) -> TaskEventExecution:
        now = assigned_at if assigned_at is not None else now_epoch()
        row = SqlTaskEventExecution(
            id=execution_id,
            task_item_id=task_item_id,
            task_id=task_id,
            agent_queue_item_id=agent_queue_item_id,
            conversation_id=conversation_id,
            status=encode_task_event_execution_status(status),
            attempt_no=attempt_no,
            assigned_at=now,
            created_at=now,
            updated_at=None,
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _execution_to_entity(row)

    def get_execution(self, execution_id: str) -> TaskEventExecution | None:
        with self._session() as session:
            row = session.get(SqlTaskEventExecution, (current_workspace_id(), execution_id))
            if row is None:
                return None
            return _execution_to_entity(row)

    def get_execution_by_agent_queue_item_id(
        self,
        agent_queue_item_id: str,
    ) -> TaskEventExecution | None:
        with self._session() as session:
            stmt = (
                select(SqlTaskEventExecution)
                .where(SqlTaskEventExecution.workspace_id == current_workspace_id())
                .where(SqlTaskEventExecution.agent_queue_item_id == agent_queue_item_id)
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            return _execution_to_entity(row) if row is not None else None

    def get_execution_by_conversation_id(
        self,
        conversation_id: str,
    ) -> TaskEventExecution | None:
        with self._session() as session:
            stmt = (
                select(SqlTaskEventExecution)
                .where(SqlTaskEventExecution.workspace_id == current_workspace_id())
                .where(SqlTaskEventExecution.conversation_id == conversation_id)
                .order_by(desc(SqlTaskEventExecution.created_at), desc(SqlTaskEventExecution.id))
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _execution_to_entity(row)

    def update_execution(
        self,
        execution_id: str,
        *,
        status: str | None = None,
        conversation_id: str | None = _UNSET,
        started_at: int | None = None,
        finished_at: int | None = None,
        result_summary: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
    ) -> TaskEventExecution | None:
        with self._session() as session:
            row = session.get(SqlTaskEventExecution, (current_workspace_id(), execution_id))
            if row is None:
                return None
            changed = False
            if status is not None:
                encoded_status = encode_task_event_execution_status(status)
                if row.status != encoded_status:
                    row.status = encoded_status
                    changed = True
            if conversation_id is not _UNSET and row.conversation_id != conversation_id:
                row.conversation_id = conversation_id
                changed = True
            if started_at is not None and row.started_at != started_at:
                row.started_at = started_at
                changed = True
            if finished_at is not None and row.finished_at != finished_at:
                row.finished_at = finished_at
                changed = True
            if result_summary is not None and row.result_summary != result_summary:
                row.result_summary = result_summary
                changed = True
            if error is not None and row.error != error:
                row.error = error
                changed = True
            if error_code is not None and row.error_code != error_code:
                row.error_code = error_code
                changed = True
            if changed:
                row.updated_at = now_epoch()
            session.flush()
            return _execution_to_entity(row)

    def list_executions_for_task(self, task_id: str) -> list[TaskEventExecution]:
        with self._session() as session:
            stmt = (
                select(SqlTaskEventExecution)
                .where(SqlTaskEventExecution.workspace_id == current_workspace_id())
                .where(SqlTaskEventExecution.task_id == task_id)
                .order_by(desc(SqlTaskEventExecution.created_at), desc(SqlTaskEventExecution.id))
            )
            rows = session.execute(stmt).scalars().all()
            return [_execution_to_entity(row) for row in rows]

    def list_executions_for_item(self, task_item_id: str) -> list[TaskEventExecution]:
        with self._session() as session:
            stmt = (
                select(SqlTaskEventExecution)
                .where(SqlTaskEventExecution.workspace_id == current_workspace_id())
                .where(SqlTaskEventExecution.task_item_id == task_item_id)
                .order_by(asc(SqlTaskEventExecution.attempt_no), asc(SqlTaskEventExecution.id))
            )
            rows = session.execute(stmt).scalars().all()
            return [_execution_to_entity(row) for row in rows]

    def list_executions_by_status(self, status: str) -> list[TaskEventExecution]:
        encoded = encode_task_event_execution_status(status)
        with self._session() as session:
            stmt = (
                select(SqlTaskEventExecution)
                .where(SqlTaskEventExecution.workspace_id == current_workspace_id())
                .where(SqlTaskEventExecution.status == encoded)
                .order_by(asc(SqlTaskEventExecution.created_at), asc(SqlTaskEventExecution.id))
            )
            rows = session.execute(stmt).scalars().all()
            return [_execution_to_entity(row) for row in rows]

    def purge_old_events(
        self,
        *,
        before_ts: int,
        states: list[str],
        event_type: str | None = None,
    ) -> int:
        from sqlalchemy import delete

        encoded_states = [encode_task_event_state(s) for s in states]
        with self._session() as session:
            stmt = (
                delete(SqlTaskEvent)
                .where(SqlTaskEvent.workspace_id == current_workspace_id())
                .where(SqlTaskEvent.created_at < before_ts)
                .where(SqlTaskEvent.state.in_(encoded_states))
            )
            if event_type is not None:
                stmt = stmt.where(SqlTaskEvent.event_type == event_type)
            result = session.execute(stmt)
            session.flush()
            return result.rowcount or 0
