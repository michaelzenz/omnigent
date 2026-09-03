"""SQLAlchemy-backed task-item store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, asc, delete, desc, exists, false, func, or_, select, update

from omnigent.db.db_models import (
    SqlFyiCluster,
    SqlFyiClusterEvent,
    SqlTaskEvent,
    SqlTaskItem,
    SqlTaskItemEvent,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_fyi_cluster_state,
    decode_task_event_state,
    decode_task_item_state,
    encode_task_event_state,
    encode_fyi_cluster_state,
    encode_task_item_state,
)
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import FyiCluster, TaskItem, TaskItemEvent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.task_item_store import TaskItemStore

_UNSET: Any = object()


def _item_to_entity(row: SqlTaskItem) -> TaskItem:
    return TaskItem(
        id=row.id,
        task_id=row.task_id,
        title=row.title,
        state=decode_task_item_state(row.state),
        created_at=row.created_at,
        description=row.description,
        instructions=row.instructions,
        internal_note=row.internal_note,
        worker_id=row.worker_id,
        created_by=row.created_by,
        kind=row.kind,
        updated_at=row.updated_at,
    )


def _item_event_to_entity(row: SqlTaskItemEvent) -> TaskItemEvent:
    return TaskItemEvent(
        task_item_id=row.task_item_id,
        event_id=row.event_id,
        relation=row.relation,
        created_at=row.created_at,
    )


def _fyi_cluster_to_entity(row: SqlFyiCluster) -> FyiCluster:
    return FyiCluster(
        id=row.id,
        owner_user_id=row.owner_user_id,
        headline=row.headline,
        rationale=row.rationale,
        state=decode_fyi_cluster_state(row.state),
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


class SqlAlchemyTaskItemStore(TaskItemStore):
    """SQLAlchemy-backed implementation of :class:`TaskItemStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)
        self._claim_session = make_managed_session_maker(self._engine, immediate=True)

    @staticmethod
    def _event_assignment_is_acceptable(
        event: SqlTaskEvent,
        *,
        task_id: str,
        manager_conversation_id: str | None,
        allow_unassigned: bool,
    ) -> bool:
        if event.task_id == task_id:
            return event.manager_conversation_id == manager_conversation_id
        if (
            event.task_id is None
            and manager_conversation_id is not None
            and event.manager_conversation_id == manager_conversation_id
        ):
            return True
        return (
            allow_unassigned
            and event.task_id is None
            and event.manager_conversation_id is None
        )

    def _claim_events(
        self,
        session,
        *,
        task_id: str,
        owner_user_id: str | None,
        manager_conversation_id: str | None,
        event_ids: list[str],
        allow_unassigned: bool,
    ) -> tuple[list[str], int]:
        """Validate and transition events; the caller owns the transaction."""
        unique_ids = list(dict.fromkeys(event_ids))
        workspace_id = current_workspace_id()
        rows = session.execute(
            select(SqlTaskEvent).where(
                SqlTaskEvent.workspace_id == workspace_id,
                SqlTaskEvent.id.in_(unique_ids),
            )
        ).scalars().all()
        rows_by_id = {row.id: row for row in rows}
        if len(rows_by_id) != len(unique_ids):
            raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)

        normalized_owner = owner_user_id or "__anonymous__"
        for event_id in unique_ids:
            if (rows_by_id[event_id].owner_user_id or "__anonymous__") != normalized_owner:
                raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
            if not self._event_assignment_is_acceptable(
                rows_by_id[event_id],
                task_id=task_id,
                manager_conversation_id=manager_conversation_id,
                allow_unassigned=allow_unassigned,
            ):
                raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)

        claimed_by_item = set(
            session.execute(
                select(SqlTaskItemEvent.event_id).where(
                    SqlTaskItemEvent.workspace_id == workspace_id,
                    SqlTaskItemEvent.event_id.in_(unique_ids),
                )
            ).scalars()
        )
        claimed_by_fyi = set(
            session.execute(
                select(SqlFyiClusterEvent.event_id)
                .join(
                    SqlFyiCluster,
                    (SqlFyiClusterEvent.workspace_id == SqlFyiCluster.workspace_id)
                    & (SqlFyiClusterEvent.cluster_id == SqlFyiCluster.id),
                )
                .where(
                    SqlFyiClusterEvent.workspace_id == workspace_id,
                    SqlFyiClusterEvent.event_id.in_(unique_ids),
                    SqlFyiCluster.state == encode_fyi_cluster_state("pending"),
                )
            ).scalars()
        )
        if claimed_by_item or claimed_by_fyi:
            raise OmnigentError(
                "Task event is already reconciled",
                code=ErrorCode.CONFLICT,
            )

        acceptable_states = [encode_task_event_state("routed")]
        if allow_unassigned:
            acceptable_states.append(encode_task_event_state("awaiting_grouping"))
        invalid_state = next(
            (
                rows_by_id[event_id]
                for event_id in unique_ids
                if rows_by_id[event_id].state not in acceptable_states
            ),
            None,
        )
        if invalid_state is not None:
            raise OmnigentError(
                f"Cannot reconcile event in state {decode_task_event_state(invalid_state.state)!r}",
                code=ErrorCode.CONFLICT,
            )

        manager_route = (
            and_(
                SqlTaskEvent.task_id.is_(None),
                SqlTaskEvent.manager_conversation_id == manager_conversation_id,
            )
            if manager_conversation_id is not None
            else false()
        )
        legacy_unassigned = (
            and_(
                SqlTaskEvent.task_id.is_(None),
                SqlTaskEvent.manager_conversation_id.is_(None),
            )
            if allow_unassigned
            else false()
        )
        item_claim_exists = exists(
            select(SqlTaskItemEvent.event_id).where(
                SqlTaskItemEvent.workspace_id == SqlTaskEvent.workspace_id,
                SqlTaskItemEvent.event_id == SqlTaskEvent.id,
            )
        )
        fyi_claim_exists = exists(
            select(SqlFyiClusterEvent.event_id)
            .join(
                SqlFyiCluster,
                (SqlFyiClusterEvent.workspace_id == SqlFyiCluster.workspace_id)
                & (SqlFyiClusterEvent.cluster_id == SqlFyiCluster.id),
            )
            .where(
                SqlFyiClusterEvent.workspace_id == SqlTaskEvent.workspace_id,
                SqlFyiClusterEvent.event_id == SqlTaskEvent.id,
                SqlFyiCluster.state == encode_fyi_cluster_state("pending"),
            )
        )
        now = now_epoch()
        result = session.execute(
            update(SqlTaskEvent)
            .where(
                SqlTaskEvent.workspace_id == workspace_id,
                SqlTaskEvent.id.in_(unique_ids),
                func.coalesce(SqlTaskEvent.owner_user_id, "__anonymous__")
                == normalized_owner,
                SqlTaskEvent.state.in_(acceptable_states),
                or_(
                    and_(
                        SqlTaskEvent.task_id == task_id,
                        SqlTaskEvent.manager_conversation_id
                        == manager_conversation_id,
                    ),
                    manager_route,
                    legacy_unassigned,
                ),
                ~item_claim_exists,
                ~fyi_claim_exists,
            )
            .values(
                task_id=task_id,
                manager_conversation_id=manager_conversation_id,
                state=encode_task_event_state("reconciled"),
                processed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != len(unique_ids):
            raise OmnigentError(
                "Task event is already reconciled",
                code=ErrorCode.CONFLICT,
            )
        return unique_ids, now

    def create_item(
        self,
        item_id: str,
        task_id: str,
        title: str,
        *,
        state: str = "draft",
        description: str | None = None,
        instructions: str | None = None,
        internal_note: str | None = None,
        worker_id: str | None = None,
        created_by: str = "manager",
        kind: str = "work",
    ) -> TaskItem:
        row = SqlTaskItem(
            id=item_id,
            task_id=task_id,
            title=title,
            state=encode_task_item_state(state),
            description=description,
            instructions=instructions,
            internal_note=internal_note,
            worker_id=worker_id,
            created_by=created_by,
            kind=kind,
            created_at=now_epoch(),
            updated_at=None,
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _item_to_entity(row)

    def create_item_with_event_claims(
        self,
        item_id: str,
        task_id: str,
        title: str,
        event_ids: list[str],
        *,
        owner_user_id: str | None,
        manager_conversation_id: str | None,
        state: str = "draft",
        description: str | None = None,
        instructions: str | None = None,
        internal_note: str | None = None,
        worker_id: str | None = None,
        created_by: str = "manager",
        kind: str = "work",
        allow_unassigned: bool = False,
    ) -> TaskItem:
        if not event_ids:
            raise ValueError("event_ids must not be empty")
        with self._claim_session() as session:
            unique_ids, now = self._claim_events(
                session,
                task_id=task_id,
                owner_user_id=owner_user_id,
                manager_conversation_id=manager_conversation_id,
                event_ids=event_ids,
                allow_unassigned=allow_unassigned,
            )
            row = SqlTaskItem(
                id=item_id,
                task_id=task_id,
                title=title,
                state=encode_task_item_state(state),
                description=description,
                instructions=instructions,
                internal_note=internal_note,
                worker_id=worker_id,
                created_by=created_by,
                kind=kind,
                created_at=now,
                updated_at=None,
            )
            session.add(row)
            session.add_all(
                SqlTaskItemEvent(
                    task_item_id=item_id,
                    event_id=event_id,
                    relation="triggered",
                    created_at=now,
                )
                for event_id in unique_ids
            )
            session.flush()
            return _item_to_entity(row)

    def get_item(self, item_id: str) -> TaskItem | None:
        with self._session() as session:
            row = session.get(SqlTaskItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            return _item_to_entity(row)

    def list_items_by_state(
        self,
        state: str,
        *,
        created_by: str | None = None,
    ) -> list[TaskItem]:
        with self._session() as session:
            stmt = select(SqlTaskItem).where(
                SqlTaskItem.workspace_id == current_workspace_id(),
            )
            stmt = stmt.where(SqlTaskItem.state == encode_task_item_state(state))
            if created_by is not None:
                stmt = stmt.where(SqlTaskItem.created_by == created_by)
            stmt = stmt.order_by(desc(SqlTaskItem.created_at), desc(SqlTaskItem.id))
            rows = session.execute(stmt).scalars().all()
            return [_item_to_entity(row) for row in rows]

    def get_item_for_event(self, event_id: str) -> TaskItem | None:
        with self._session() as session:
            stmt = (
                select(SqlTaskItem)
                .join(
                    SqlTaskItemEvent,
                    (SqlTaskItemEvent.workspace_id == SqlTaskItem.workspace_id)
                    & (SqlTaskItemEvent.task_item_id == SqlTaskItem.id),
                )
                .where(SqlTaskItemEvent.workspace_id == current_workspace_id())
                .where(SqlTaskItemEvent.event_id == event_id)
                .order_by(desc(SqlTaskItem.created_at), desc(SqlTaskItem.id))
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _item_to_entity(row)

    def get_event_ids_claimed_by_items(self, event_ids: list[str]) -> set[str]:
        if not event_ids:
            return set()
        with self._session() as session:
            stmt = (
                select(SqlTaskItemEvent.event_id)
                .where(SqlTaskItemEvent.workspace_id == current_workspace_id())
                .where(SqlTaskItemEvent.event_id.in_(event_ids))
            )
            return {row[0] for row in session.execute(stmt).all()}

    def list_items_for_task(
        self,
        task_id: str,
        *,
        state: str | None = None,
    ) -> list[TaskItem]:
        with self._session() as session:
            stmt = (
                select(SqlTaskItem)
                .where(SqlTaskItem.workspace_id == current_workspace_id())
                .where(SqlTaskItem.task_id == task_id)
            )
            if state is not None:
                stmt = stmt.where(SqlTaskItem.state == encode_task_item_state(state))
            stmt = stmt.order_by(
                asc(SqlTaskItem.created_at),
                asc(SqlTaskItem.id),
            )
            rows = session.execute(stmt).scalars().all()
            return [_item_to_entity(row) for row in rows]

    def delete_items_for_task(self, task_id: str, *, exclude_states: set[str] | None = None) -> int:
        with self._session() as session:
            stmt = delete(SqlTaskItem).where(
                SqlTaskItem.workspace_id == current_workspace_id(),
                SqlTaskItem.task_id == task_id,
            )
            if exclude_states:
                excluded_codes = [encode_task_item_state(s) for s in exclude_states]
                stmt = stmt.where(~SqlTaskItem.state.in_(excluded_codes))
            result = session.execute(stmt)
            session.flush()
            return result.rowcount or 0

    def update_item(
        self,
        item_id: str,
        *,
        title: str | None = None,
        state: str | None = None,
        instructions: str | None = _UNSET,
        description: str | None = _UNSET,
        internal_note: str | None = _UNSET,
        worker_id: str | None = _UNSET,
        task_id: str | None = None,
    ) -> TaskItem | None:
        with self._session() as session:
            row = session.get(SqlTaskItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            if title is not None:
                row.title = title
            if state is not None:
                row.state = encode_task_item_state(state)
            if task_id is not None:
                row.task_id = task_id
            if instructions is not _UNSET:
                row.instructions = instructions
            if description is not _UNSET:
                row.description = description
            if internal_note is not _UNSET:
                row.internal_note = internal_note
            if worker_id is not _UNSET:
                row.worker_id = worker_id
            row.updated_at = now_epoch()
            session.flush()
            return _item_to_entity(row)

    def link_event(
        self,
        task_item_id: str,
        event_id: str,
        *,
        relation: str = "triggered",
    ) -> TaskItemEvent:
        with self._claim_session() as session:
            existing = session.execute(
                select(SqlTaskItemEvent).where(
                    SqlTaskItemEvent.workspace_id == current_workspace_id(),
                    SqlTaskItemEvent.event_id == event_id,
                )
            ).scalars().first()
            if existing is not None:
                if existing.task_item_id == task_item_id:
                    return _item_event_to_entity(existing)
                raise OmnigentError(
                    "Task event is already reconciled",
                    code=ErrorCode.CONFLICT,
                )
            row = SqlTaskItemEvent(
                task_item_id=task_item_id,
                event_id=event_id,
                relation=relation,
                created_at=now_epoch(),
            )
            session.add(row)
            session.flush()
            return _item_event_to_entity(row)

    def update_item_with_event_claims(
        self,
        task_item_id: str,
        task_id: str,
        event_ids: list[str],
        *,
        owner_user_id: str | None,
        manager_conversation_id: str | None,
        title: str | None = None,
        description: str | None = _UNSET,
        instructions: str | None = _UNSET,
        internal_note: str | None = _UNSET,
        relation: str = "triggered",
        allow_unassigned: bool = False,
    ) -> TaskItem:
        if not event_ids:
            raise ValueError("event_ids must not be empty")
        with self._claim_session() as session:
            row = session.get(
                SqlTaskItem,
                (current_workspace_id(), task_item_id),
            )
            if row is None or row.task_id != task_id:
                raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
            if decode_task_item_state(row.state) not in {"pending", "queued"}:
                raise OmnigentError(
                    f"Cannot extend item in state {decode_task_item_state(row.state)!r}",
                    code=ErrorCode.CONFLICT,
                )
            unique_ids, now = self._claim_events(
                session,
                task_id=task_id,
                owner_user_id=owner_user_id,
                manager_conversation_id=manager_conversation_id,
                event_ids=event_ids,
                allow_unassigned=allow_unassigned,
            )
            if title is not None:
                row.title = title
            if description is not _UNSET:
                row.description = description
            if instructions is not _UNSET:
                row.instructions = instructions
            if internal_note is not _UNSET:
                row.internal_note = internal_note
            row.updated_at = now
            session.add_all(
                SqlTaskItemEvent(
                    task_item_id=task_item_id,
                    event_id=event_id,
                    relation=relation,
                    created_at=now,
                )
                for event_id in unique_ids
            )
            session.flush()
            return _item_to_entity(row)

    def list_events_for_item(self, task_item_id: str) -> list[TaskItemEvent]:
        with self._session() as session:
            stmt = (
                select(SqlTaskItemEvent)
                .where(SqlTaskItemEvent.workspace_id == current_workspace_id())
                .where(SqlTaskItemEvent.task_item_id == task_item_id)
                .order_by(asc(SqlTaskItemEvent.created_at), asc(SqlTaskItemEvent.event_id))
            )
            rows = session.execute(stmt).scalars().all()
            return [_item_event_to_entity(row) for row in rows]

    def unlink_events(self, task_item_id: str) -> int:
        with self._session() as session:
            result = session.execute(
                delete(SqlTaskItemEvent).where(
                    SqlTaskItemEvent.workspace_id == current_workspace_id(),
                    SqlTaskItemEvent.task_item_id == task_item_id,
                )
            )
            return result.rowcount

    def create_fyi_cluster(
        self,
        cluster_id: str,
        owner_user_id: str,
        headline: str,
        *,
        rationale: str | None = None,
        state: str = "pending",
    ) -> FyiCluster:
        row = SqlFyiCluster(
            id=cluster_id,
            owner_user_id=owner_user_id,
            headline=headline,
            rationale=rationale,
            state=encode_fyi_cluster_state(state),
            created_at=now_epoch(),
            resolved_at=None,
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _fyi_cluster_to_entity(row)

    def get_fyi_cluster(self, cluster_id: str) -> FyiCluster | None:
        with self._session() as session:
            row = session.get(SqlFyiCluster, (current_workspace_id(), cluster_id))
            if row is None:
                return None
            return _fyi_cluster_to_entity(row)

    def get_fyi_cluster_for_event(self, event_id: str) -> FyiCluster | None:
        with self._session() as session:
            stmt = (
                select(SqlFyiCluster)
                .join(
                    SqlFyiClusterEvent,
                    (SqlFyiClusterEvent.workspace_id == SqlFyiCluster.workspace_id)
                    & (SqlFyiClusterEvent.cluster_id == SqlFyiCluster.id),
                )
                .where(SqlFyiClusterEvent.workspace_id == current_workspace_id())
                .where(SqlFyiClusterEvent.event_id == event_id)
                .where(
                    SqlFyiCluster.state == encode_fyi_cluster_state("pending"),
                )
                .order_by(desc(SqlFyiCluster.created_at), desc(SqlFyiCluster.id))
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _fyi_cluster_to_entity(row)

    def get_event_ids_claimed_by_fyi_clusters(self, event_ids: list[str]) -> set[str]:
        if not event_ids:
            return set()
        with self._session() as session:
            stmt = (
                select(SqlFyiClusterEvent.event_id)
                .join(
                    SqlFyiCluster,
                    (SqlFyiClusterEvent.workspace_id == SqlFyiCluster.workspace_id)
                    & (SqlFyiClusterEvent.cluster_id == SqlFyiCluster.id),
                )
                .where(SqlFyiClusterEvent.workspace_id == current_workspace_id())
                .where(SqlFyiClusterEvent.event_id.in_(event_ids))
                .where(
                    SqlFyiCluster.state == encode_fyi_cluster_state("pending"),
                )
            )
            return {row[0] for row in session.execute(stmt).all()}

    def list_fyi_clusters(
        self,
        *,
        owner_user_id: str | None = None,
        state: str | None = None,
    ) -> list[FyiCluster]:
        with self._session() as session:
            stmt = select(SqlFyiCluster).where(
                SqlFyiCluster.workspace_id == current_workspace_id(),
            )
            if owner_user_id is not None:
                stmt = stmt.where(SqlFyiCluster.owner_user_id == owner_user_id)
            if state is not None:
                stmt = stmt.where(SqlFyiCluster.state == encode_fyi_cluster_state(state))
            stmt = stmt.order_by(desc(SqlFyiCluster.created_at), desc(SqlFyiCluster.id))
            rows = session.execute(stmt).scalars().all()
            return [_fyi_cluster_to_entity(row) for row in rows]

    def update_fyi_cluster(
        self,
        cluster_id: str,
        *,
        state: str | None = None,
        headline: str | None = None,
        rationale: str | None = None,
        resolved_at: int | None = None,
    ) -> FyiCluster | None:
        with self._session() as session:
            row = session.get(SqlFyiCluster, (current_workspace_id(), cluster_id))
            if row is None:
                return None
            if state is not None:
                row.state = encode_fyi_cluster_state(state)
            if headline is not None:
                row.headline = headline
            if rationale is not None:
                row.rationale = rationale
            if resolved_at is not None:
                row.resolved_at = resolved_at
            session.flush()
            return _fyi_cluster_to_entity(row)

    def link_fyi_cluster_event(self, cluster_id: str, event_id: str) -> None:
        row = SqlFyiClusterEvent(cluster_id=cluster_id, event_id=event_id)
        with self._session() as session:
            session.merge(row)

    def list_fyi_cluster_event_ids(self, cluster_id: str) -> list[str]:
        with self._session() as session:
            stmt = (
                select(SqlFyiClusterEvent.event_id)
                .where(SqlFyiClusterEvent.workspace_id == current_workspace_id())
                .where(SqlFyiClusterEvent.cluster_id == cluster_id)
                .order_by(asc(SqlFyiClusterEvent.event_id))
            )
            return list(session.execute(stmt).scalars().all())
