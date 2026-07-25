"""SQLAlchemy-backed task-item store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc, select

from omnigent.db.db_models import (
    SqlFyiCluster,
    SqlFyiClusterEvent,
    SqlGroupingProposal,
    SqlGroupingProposalEvent,
    SqlTaskItem,
    SqlTaskItemEvent,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_fyi_cluster_state,
    decode_grouping_proposal_state,
    decode_task_item_state,
    encode_fyi_cluster_state,
    encode_grouping_proposal_state,
    encode_task_item_state,
)
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import FyiCluster, GroupingProposal, TaskItem, TaskItemEvent
from omnigent.stores.task_item_store import TaskItemStore

_UNSET: Any = object()
_OPEN_ITEM_STATES = frozenset({"draft", "awaiting_user_ack", "approved", "queued", "running"})


def _item_to_entity(row: SqlTaskItem) -> TaskItem:
    return TaskItem(
        id=row.id,
        task_id=row.task_id,
        title=row.title,
        state=decode_task_item_state(row.state),
        created_at=row.created_at,
        canonical_key=row.canonical_key,
        instructions=row.instructions,
        worker_agent_id=row.worker_agent_id,
        model=row.model,
        host_id=row.host_id,
        workspace=row.workspace,
        harness=row.harness,
        priority=row.priority,
        created_by=row.created_by,
        updated_at=row.updated_at,
        routing_proposal=row.routing_proposal,
    )


def _item_event_to_entity(row: SqlTaskItemEvent) -> TaskItemEvent:
    return TaskItemEvent(
        task_item_id=row.task_item_id,
        event_id=row.event_id,
        relation=row.relation,
        created_at=row.created_at,
    )


def _proposal_to_entity(row: SqlGroupingProposal) -> GroupingProposal:
    return GroupingProposal(
        id=row.id,
        owner_user_id=row.owner_user_id,
        state=decode_grouping_proposal_state(row.state),
        payload=row.payload,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


def _fyi_cluster_to_entity(row: SqlFyiCluster) -> FyiCluster:
    return FyiCluster(
        id=row.id,
        owner_user_id=row.owner_user_id,
        canonical_key=row.canonical_key,
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
        canonical_key: str | None = None,
        instructions: str | None = None,
        worker_agent_id: str | None = None,
        model: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        harness: str | None = None,
        priority: int = 0,
        created_by: str = "manager",
        routing_proposal: str | None = None,
    ) -> TaskItem:
        row = SqlTaskItem(
            id=item_id,
            task_id=task_id,
            title=title,
            state=encode_task_item_state(state),
            canonical_key=canonical_key,
            instructions=instructions,
            worker_agent_id=worker_agent_id,
            model=model,
            host_id=host_id,
            workspace=workspace,
            harness=harness,
            priority=priority,
            created_by=created_by,
            routing_proposal=routing_proposal,
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

    def get_item_by_canonical_key(
        self,
        task_id: str,
        canonical_key: str,
    ) -> TaskItem | None:
        with self._session() as session:
            stmt = (
                select(SqlTaskItem)
                .where(SqlTaskItem.workspace_id == current_workspace_id())
                .where(SqlTaskItem.task_id == task_id)
                .where(SqlTaskItem.canonical_key == canonical_key)
                .order_by(desc(SqlTaskItem.created_at), desc(SqlTaskItem.id))
            )
            rows = session.execute(stmt).scalars().all()
            for row in rows:
                if decode_task_item_state(row.state) in _OPEN_ITEM_STATES:
                    return _item_to_entity(row)
            return None

    def get_open_routing_item_by_canonical_key(
        self,
        canonical_key: str,
    ) -> TaskItem | None:
        with self._session() as session:
            stmt = (
                select(SqlTaskItem)
                .where(SqlTaskItem.workspace_id == current_workspace_id())
                .where(SqlTaskItem.canonical_key == canonical_key)
                .where(
                    SqlTaskItem.state == encode_task_item_state("routing_proposed"),
                )
                .where(SqlTaskItem.created_by == "secretary")
                .order_by(desc(SqlTaskItem.created_at), desc(SqlTaskItem.id))
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
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

    def get_routing_item_for_event(self, event_id: str) -> TaskItem | None:
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
                .where(
                    SqlTaskItem.state == encode_task_item_state("routing_proposed"),
                )
                .order_by(desc(SqlTaskItem.created_at), desc(SqlTaskItem.id))
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _item_to_entity(row)

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
                desc(SqlTaskItem.priority),
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
        canonical_key: str | None = _UNSET,
        instructions: str | None = _UNSET,
        worker_agent_id: str | None = _UNSET,
        model: str | None = _UNSET,
        host_id: str | None = _UNSET,
        workspace: str | None = _UNSET,
        harness: str | None = _UNSET,
        priority: int | None = None,
        task_id: str | None = None,
        routing_proposal: str | None = _UNSET,
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
            if canonical_key is not _UNSET:
                row.canonical_key = canonical_key
            if instructions is not _UNSET:
                row.instructions = instructions
            if worker_agent_id is not _UNSET:
                row.worker_agent_id = worker_agent_id
            if model is not _UNSET:
                row.model = model
            if host_id is not _UNSET:
                row.host_id = host_id
            if workspace is not _UNSET:
                row.workspace = workspace
            if harness is not _UNSET:
                row.harness = harness
            if routing_proposal is not _UNSET:
                row.routing_proposal = routing_proposal
            if priority is not None:
                row.priority = priority
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

    def create_grouping_proposal(
        self,
        proposal_id: str,
        owner_user_id: str,
        payload: str,
        *,
        state: str = "awaiting_user_ack",
    ) -> GroupingProposal:
        row = SqlGroupingProposal(
            id=proposal_id,
            owner_user_id=owner_user_id,
            state=encode_grouping_proposal_state(state),
            payload=payload,
            created_at=now_epoch(),
            resolved_at=None,
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _proposal_to_entity(row)

    def get_grouping_proposal(self, proposal_id: str) -> GroupingProposal | None:
        with self._session() as session:
            row = session.get(SqlGroupingProposal, (current_workspace_id(), proposal_id))
            if row is None:
                return None
            return _proposal_to_entity(row)

    def list_grouping_proposals(
        self,
        *,
        owner_user_id: str | None = None,
        state: str | None = None,
    ) -> list[GroupingProposal]:
        with self._session() as session:
            stmt = select(SqlGroupingProposal).where(
                SqlGroupingProposal.workspace_id == current_workspace_id()
            )
            if owner_user_id is not None:
                stmt = stmt.where(SqlGroupingProposal.owner_user_id == owner_user_id)
            if state is not None:
                stmt = stmt.where(
                    SqlGroupingProposal.state == encode_grouping_proposal_state(state)
                )
            stmt = stmt.order_by(desc(SqlGroupingProposal.created_at), desc(SqlGroupingProposal.id))
            rows = session.execute(stmt).scalars().all()
            return [_proposal_to_entity(row) for row in rows]

    def update_grouping_proposal(
        self,
        proposal_id: str,
        *,
        state: str | None = None,
        payload: str | None = None,
        resolved_at: int | None = None,
    ) -> GroupingProposal | None:
        with self._session() as session:
            row = session.get(SqlGroupingProposal, (current_workspace_id(), proposal_id))
            if row is None:
                return None
            if state is not None:
                row.state = encode_grouping_proposal_state(state)
            if payload is not None:
                row.payload = payload
            if resolved_at is not None:
                row.resolved_at = resolved_at
            session.flush()
            return _proposal_to_entity(row)

    def link_proposal_event(self, proposal_id: str, event_id: str) -> None:
        row = SqlGroupingProposalEvent(proposal_id=proposal_id, event_id=event_id)
        with self._session() as session:
            session.merge(row)

    def list_proposal_event_ids(self, proposal_id: str) -> list[str]:
        with self._session() as session:
            stmt = (
                select(SqlGroupingProposalEvent.event_id)
                .where(SqlGroupingProposalEvent.workspace_id == current_workspace_id())
                .where(SqlGroupingProposalEvent.proposal_id == proposal_id)
                .order_by(asc(SqlGroupingProposalEvent.event_id))
            )
            return list(session.execute(stmt).scalars().all())

    def create_fyi_cluster(
        self,
        cluster_id: str,
        owner_user_id: str,
        headline: str,
        *,
        canonical_key: str | None = None,
        rationale: str | None = None,
        state: str = "awaiting_user_ack",
    ) -> FyiCluster:
        row = SqlFyiCluster(
            id=cluster_id,
            owner_user_id=owner_user_id,
            canonical_key=canonical_key,
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

    def get_open_fyi_cluster_by_canonical_key(
        self,
        canonical_key: str,
    ) -> FyiCluster | None:
        with self._session() as session:
            stmt = (
                select(SqlFyiCluster)
                .where(SqlFyiCluster.workspace_id == current_workspace_id())
                .where(SqlFyiCluster.canonical_key == canonical_key)
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
