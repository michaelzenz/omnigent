"""SQLAlchemy-backed task-item store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc, select

from omnigent.db.db_models import (
    SqlFyiCluster,
    SqlFyiClusterEvent,
    SqlTaskItem,
    SqlTaskItemEvent,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_fyi_cluster_state,
    decode_task_item_state,
    encode_fyi_cluster_state,
    encode_task_item_state,
)
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import FyiCluster, TaskItem, TaskItemEvent
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
            created_at=now_epoch(),
            updated_at=None,
        )
        with self._session() as session:
            session.add(row)
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
        row = SqlTaskItemEvent(
            task_item_id=task_item_id,
            event_id=event_id,
            relation=relation,
            created_at=now_epoch(),
        )
        with self._session() as session:
            session.merge(row)
            session.flush()
            return _item_event_to_entity(row)

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

    def create_fyi_cluster(
        self,
        cluster_id: str,
        owner_user_id: str,
        headline: str,
        *,
        rationale: str | None = None,
        state: str = "awaiting_user_ack",
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
                    SqlFyiCluster.state == encode_fyi_cluster_state("awaiting_user_ack"),
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
                    SqlFyiCluster.state == encode_fyi_cluster_state("awaiting_user_ack"),
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
