"""SQLAlchemy-backed task-event store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, delete, desc, select

from omnigent.db.db_models import (
    SqlTaskEvent,
    SqlTaskEventExecution,
    SqlTaskEventRoutingAttempt,
    SqlTaskEventRoutingResolution,
    SqlTaskSessionBinding,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_task_event_execution_status,
    decode_task_event_routing_decision,
    decode_task_event_state,
    encode_task_event_execution_status,
    encode_task_event_routing_decision,
    encode_task_event_state,
)
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import (
    TaskEvent,
    TaskEventExecution,
    TaskEventRoutingAttempt,
    TaskEventRoutingResolution,
    TaskEventTag,
    TaskSessionBinding,
)
from omnigent.stores.agent_task.tags import decode_event_tags, encode_event_tags
from omnigent.stores.task_event_store import TASK_SESSION_BINDING_KINDS, TaskEventStore

_UNSET: Any = object()


def _event_to_entity(row: SqlTaskEvent) -> TaskEvent:
    return TaskEvent(
        id=row.id,
        event_type=row.event_type,
        title=row.title,
        tags=decode_event_tags(row.id, row.tags),
        state=decode_task_event_state(row.state),
        priority=row.priority,
        created_at=row.created_at,
        task_id=row.task_id,
        payload=row.payload,
        source=row.source,
        summary=row.summary,
        selected_routing_attempt_id=row.selected_routing_attempt_id,
        manager_agent_id=row.manager_agent_id,
        manager_conversation_id=row.manager_conversation_id,
        source_key=row.source_key,
        source_offset=row.source_offset,
        source_session_id=row.source_session_id,
        updated_at=row.updated_at,
        routed_at=row.routed_at,
        processed_at=row.processed_at,
    )


def _attempt_to_entity(row: SqlTaskEventRoutingAttempt) -> TaskEventRoutingAttempt:
    return TaskEventRoutingAttempt(
        id=row.id,
        event_id=row.event_id,
        candidate_task_id=row.candidate_task_id,
        candidate_manager_agent_id=row.candidate_manager_agent_id,
        rank=row.rank,
        decision=decode_task_event_routing_decision(row.decision),
        proposed_at=row.proposed_at,
        score=row.score,
        manager_reason=row.manager_reason,
        responded_at=row.responded_at,
        selected_at=row.selected_at,
    )


def _resolution_to_entity(row: SqlTaskEventRoutingResolution) -> TaskEventRoutingResolution:
    return TaskEventRoutingResolution(
        id=row.id,
        event_id=row.event_id,
        selected_attempt_id=row.selected_attempt_id,
        selected_task_id=row.selected_task_id,
        selected_manager_agent_id=row.selected_manager_agent_id,
        created_at=row.created_at,
        resolved_by_user_id=row.resolved_by_user_id,
        resolution_note=row.resolution_note,
    )


def _execution_to_entity(row: SqlTaskEventExecution) -> TaskEventExecution:
    return TaskEventExecution(
        id=row.id,
        task_item_id=row.task_item_id,
        event_id=row.event_id,
        task_id=row.task_id,
        manager_agent_id=row.manager_agent_id,
        worker_agent_id=row.worker_agent_id,
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


def _binding_to_entity(row: SqlTaskSessionBinding) -> TaskSessionBinding:
    return TaskSessionBinding(
        session_id=row.session_id,
        task_id=row.task_id,
        manager_agent_id=row.manager_agent_id,
        binding_kind=row.binding_kind,
        created_at=row.created_at,
        manager_conversation_id=row.manager_conversation_id,
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
        source_session_id: str | None = None,
        summary: str | None = None,
        state: str = "received",
        priority: int = 0,
        manager_agent_id: str | None = None,
        manager_conversation_id: str | None = None,
        tags: list[TaskEventTag] | None = None,
    ) -> TaskEvent:
        tag_rows = tags or []
        normalized_tags = [
            TaskEventTag(event_id=event_id, tag_type=tag.tag_type, tag=tag.tag)
            for tag in tag_rows
        ]
        row = SqlTaskEvent(
            id=event_id,
            task_id=task_id,
            manager_agent_id=manager_agent_id,
            manager_conversation_id=manager_conversation_id,
            event_type=event_type,
            title=title,
            payload=payload,
            source=source,
            source_key=source_key,
            source_offset=source_offset,
            source_session_id=source_session_id,
            tags=encode_event_tags(normalized_tags),
            summary=summary,
            state=encode_task_event_state(state),
            priority=priority,
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
        manager_agent_id: str | None = None,
    ) -> list[TaskEvent]:
        with self._session() as session:
            stmt = select(SqlTaskEvent).where(SqlTaskEvent.workspace_id == current_workspace_id())
            if state is not None:
                stmt = stmt.where(SqlTaskEvent.state == encode_task_event_state(state))
            if task_id is not None:
                stmt = stmt.where(SqlTaskEvent.task_id == task_id)
            if manager_agent_id is not None:
                stmt = stmt.where(SqlTaskEvent.manager_agent_id == manager_agent_id)
            stmt = stmt.order_by(desc(SqlTaskEvent.created_at), desc(SqlTaskEvent.id))
            rows = session.execute(stmt).scalars().all()
            return [_event_to_entity(row) for row in rows]

    def update_event(
        self,
        event_id: str,
        *,
        task_id: str | None = _UNSET,
        state: str | None = None,
        priority: int | None = None,
        selected_routing_attempt_id: str | None = _UNSET,
        manager_agent_id: str | None = _UNSET,
        manager_conversation_id: str | None = _UNSET,
        routed_at: int | None = None,
        processed_at: int | None = None,
    ) -> TaskEvent | None:
        with self._session() as session:
            row = session.get(SqlTaskEvent, (current_workspace_id(), event_id))
            if row is None:
                return None
            changed = False
            if task_id is not _UNSET and row.task_id != task_id:
                row.task_id = task_id
                changed = True
            if state is not None:
                encoded_state = encode_task_event_state(state)
                if row.state != encoded_state:
                    row.state = encoded_state
                    changed = True
            if priority is not None and row.priority != priority:
                row.priority = priority
                changed = True
            if selected_routing_attempt_id is not _UNSET and (
                row.selected_routing_attempt_id != selected_routing_attempt_id
            ):
                row.selected_routing_attempt_id = selected_routing_attempt_id
                changed = True
            if manager_agent_id is not _UNSET and row.manager_agent_id != manager_agent_id:
                row.manager_agent_id = manager_agent_id
                changed = True
            if manager_conversation_id is not _UNSET and (
                row.manager_conversation_id != manager_conversation_id
            ):
                row.manager_conversation_id = manager_conversation_id
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

    def get_event_tags(self, event_id: str) -> list[TaskEventTag]:
        event = self.get_event(event_id)
        if event is None:
            return []
        return list(event.tags or [])

    def create_routing_attempt(
        self,
        attempt_id: str,
        event_id: str,
        candidate_task_id: str,
        candidate_manager_agent_id: str,
        rank: int,
        *,
        score: float | None = None,
        decision: str = "proposed",
        manager_reason: str | None = None,
        proposed_at: int | None = None,
        responded_at: int | None = None,
        selected_at: int | None = None,
    ) -> TaskEventRoutingAttempt:
        row = SqlTaskEventRoutingAttempt(
            id=attempt_id,
            event_id=event_id,
            candidate_task_id=candidate_task_id,
            candidate_manager_agent_id=candidate_manager_agent_id,
            rank=rank,
            score=score,
            decision=encode_task_event_routing_decision(decision),
            manager_reason=manager_reason,
            proposed_at=proposed_at if proposed_at is not None else now_epoch(),
            responded_at=responded_at,
            selected_at=selected_at,
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _attempt_to_entity(row)

    def update_routing_attempt(
        self,
        attempt_id: str,
        *,
        decision: str | None = None,
        manager_reason: str | None = None,
        responded_at: int | None = None,
        selected_at: int | None = None,
    ) -> TaskEventRoutingAttempt | None:
        with self._session() as session:
            row = session.get(SqlTaskEventRoutingAttempt, (current_workspace_id(), attempt_id))
            if row is None:
                return None
            if decision is not None:
                row.decision = encode_task_event_routing_decision(decision)
            if manager_reason is not None:
                row.manager_reason = manager_reason
            if responded_at is not None:
                row.responded_at = responded_at
            if selected_at is not None:
                row.selected_at = selected_at
            session.flush()
            return _attempt_to_entity(row)

    def list_routing_attempts(self, event_id: str) -> list[TaskEventRoutingAttempt]:
        with self._session() as session:
            stmt = (
                select(SqlTaskEventRoutingAttempt)
                .where(SqlTaskEventRoutingAttempt.workspace_id == current_workspace_id())
                .where(SqlTaskEventRoutingAttempt.event_id == event_id)
                .order_by(asc(SqlTaskEventRoutingAttempt.rank), asc(SqlTaskEventRoutingAttempt.id))
            )
            rows = session.execute(stmt).scalars().all()
            return [_attempt_to_entity(row) for row in rows]

    def create_resolution(
        self,
        resolution_id: str,
        event_id: str,
        selected_attempt_id: str,
        selected_task_id: str,
        selected_manager_agent_id: str,
        *,
        resolved_by_user_id: str | None = None,
        resolution_note: str | None = None,
        created_at: int | None = None,
    ) -> TaskEventRoutingResolution:
        row = SqlTaskEventRoutingResolution(
            id=resolution_id,
            event_id=event_id,
            selected_attempt_id=selected_attempt_id,
            selected_task_id=selected_task_id,
            selected_manager_agent_id=selected_manager_agent_id,
            resolved_by_user_id=resolved_by_user_id,
            resolution_note=resolution_note,
            created_at=created_at if created_at is not None else now_epoch(),
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _resolution_to_entity(row)

    def get_resolution(self, event_id: str) -> TaskEventRoutingResolution | None:
        with self._session() as session:
            stmt = (
                select(SqlTaskEventRoutingResolution)
                .where(SqlTaskEventRoutingResolution.workspace_id == current_workspace_id())
                .where(SqlTaskEventRoutingResolution.event_id == event_id)
                .order_by(
                    desc(SqlTaskEventRoutingResolution.created_at),
                    desc(SqlTaskEventRoutingResolution.id),
                )
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _resolution_to_entity(row)

    def create_execution(
        self,
        execution_id: str,
        task_item_id: str,
        task_id: str,
        manager_agent_id: str,
        worker_agent_id: str,
        *,
        event_id: str | None = None,
        status: str = "queued",
        attempt_no: int = 1,
        conversation_id: str | None = None,
        assigned_at: int | None = None,
    ) -> TaskEventExecution:
        now = assigned_at if assigned_at is not None else now_epoch()
        row = SqlTaskEventExecution(
            id=execution_id,
            task_item_id=task_item_id,
            event_id=event_id,
            task_id=task_id,
            manager_agent_id=manager_agent_id,
            worker_agent_id=worker_agent_id,
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

    def list_executions_for_event(self, event_id: str) -> list[TaskEventExecution]:
        with self._session() as session:
            stmt = (
                select(SqlTaskEventExecution)
                .where(SqlTaskEventExecution.workspace_id == current_workspace_id())
                .where(SqlTaskEventExecution.event_id == event_id)
                .order_by(asc(SqlTaskEventExecution.attempt_no), asc(SqlTaskEventExecution.id))
            )
            rows = session.execute(stmt).scalars().all()
            return [_execution_to_entity(row) for row in rows]

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

    def get_binding(self, session_id: str) -> TaskSessionBinding | None:
        with self._session() as session:
            row = session.get(SqlTaskSessionBinding, (current_workspace_id(), session_id))
            if row is None:
                return None
            return _binding_to_entity(row)

    def upsert_binding(
        self,
        session_id: str,
        task_id: str,
        manager_agent_id: str,
        binding_kind: str,
        *,
        manager_conversation_id: str | None = None,
        created_at: int | None = None,
    ) -> TaskSessionBinding:
        if binding_kind not in TASK_SESSION_BINDING_KINDS:
            raise ValueError(f"unknown binding_kind: {binding_kind!r}")
        with self._session() as session:
            row = session.get(SqlTaskSessionBinding, (current_workspace_id(), session_id))
            if row is None:
                row = SqlTaskSessionBinding(
                    session_id=session_id,
                    task_id=task_id,
                    manager_agent_id=manager_agent_id,
                    manager_conversation_id=manager_conversation_id,
                    binding_kind=binding_kind,
                    created_at=created_at if created_at is not None else now_epoch(),
                )
                session.add(row)
            else:
                row.task_id = task_id
                row.manager_agent_id = manager_agent_id
                row.manager_conversation_id = manager_conversation_id
                row.binding_kind = binding_kind
            session.flush()
            return _binding_to_entity(row)

    def delete_binding(self, session_id: str) -> bool:
        with self._session() as session:
            row = session.get(SqlTaskSessionBinding, (current_workspace_id(), session_id))
            if row is None:
                return False
            session.delete(row)
            return True
