"""SQLAlchemy-backed GlobalHub FYI cluster store."""

from __future__ import annotations

import builtins
from collections.abc import Collection

from sqlalchemy import ColumnElement, and_, asc, select, update
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlFyiCluster, SqlTaskEvent, current_workspace_id
from omnigent.db.enum_codecs import (
    decode_fyi_cluster_state,
    encode_fyi_cluster_state,
    encode_task_event_state,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import FyiCluster
from omnigent.stores.fyi_cluster_store import FyiClusterStore
from omnigent.util.uuid import normalize_uuid

_PENDING = encode_fyi_cluster_state("pending")
_DISMISSED_CLUSTER = encode_fyi_cluster_state("dismissed")
_UNROUTED = encode_task_event_state("unrouted")
_ROUTED = encode_task_event_state("routed")
_FILED = encode_task_event_state("fyi")
_DISMISSED_EVENT = encode_task_event_state("dismissed")


def _to_entity(row: SqlFyiCluster) -> FyiCluster:
    return FyiCluster(
        id=row.id,
        owner_user_id=row.owner_user_id,
        headline=row.headline,
        state=decode_fyi_cluster_state(row.state),
        created_at=row.created_at,
        rationale=row.rationale,
        resolved_at=row.resolved_at,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlFyiCluster.workspace_id == current_workspace_id(),
        SqlFyiCluster.owner_user_id == owner_user_id,
    )


def _owned_row(
    session: Session, cluster_id: str, owner_user_id: str | None
) -> SqlFyiCluster | None:
    row = session.get(SqlFyiCluster, (current_workspace_id(), cluster_id))
    return row if row is not None and row.owner_user_id == owner_user_id else None


def _fileable_events(
    session: Session, event_ids: Collection[str], owner_user_id: str | None, cluster_id: str
) -> list[SqlTaskEvent] | None:
    """Return the owner's events to file into the cluster, or ``None`` if one cannot be."""
    wanted = list(dict.fromkeys(normalize_uuid(event_id) or event_id for event_id in event_ids))
    stmt = select(SqlTaskEvent).where(
        SqlTaskEvent.workspace_id == current_workspace_id(),
        SqlTaskEvent.owner_user_id == owner_user_id,
        SqlTaskEvent.id.in_(wanted),
    )
    rows = list(session.execute(stmt).scalars())
    if len(rows) != len(wanted):
        return None
    for row in rows:
        filed_here = row.state == _FILED and row.fyi_cluster_id == cluster_id
        if row.state not in (_UNROUTED, _ROUTED) and not filed_here:
            return None
    return rows


def _file(rows: list[SqlTaskEvent], cluster_id: str, now: int) -> None:
    for row in rows:
        if row.state != _FILED:
            row.state = _FILED
            row.fyi_cluster_id = cluster_id
            row.processed_at = row.processed_at or now
            row.updated_at = now


class SqlAlchemyFyiClusterStore(FyiClusterStore):
    """
    SQLAlchemy-backed implementation of :class:`FyiClusterStore`.

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
            query_name_prefix="omnigent.fyi_cluster_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.fyi_cluster_store",
            immediate=True,
        )

    def create(
        self,
        cluster_id: str,
        *,
        owner_user_id: str | None,
        headline: str,
        event_ids: Collection[str],
        rationale: str | None = None,
    ) -> FyiCluster | None:
        """Create a pending cluster and file the owner's events into it, all or none."""
        now = now_epoch()

        def write(session: Session) -> FyiCluster | None:
            require_active_account(session, owner_user_id)
            events = _fileable_events(session, event_ids, owner_user_id, cluster_id)
            if events is None:
                return None
            row = SqlFyiCluster(
                id=cluster_id,
                owner_user_id=owner_user_id,
                headline=headline,
                rationale=rationale,
                state=_PENDING,
                created_at=now,
                resolved_at=None,
            )
            session.add(row)
            _file(events, cluster_id, now)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "insert_fyi_cluster", write)

    def extend(
        self,
        cluster_id: str,
        *,
        owner_user_id: str | None,
        headline: str,
        event_ids: Collection[str],
        rationale: str | None = None,
    ) -> FyiCluster | None:
        """File more of the owner's events into their pending cluster, all or none."""
        now = now_epoch()

        def write(session: Session) -> FyiCluster | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, cluster_id, owner_user_id)
            if row is None or row.state != _PENDING:
                return None
            events = _fileable_events(session, event_ids, owner_user_id, row.id)
            if events is None:
                return None
            row.headline = headline
            if rationale is not None:
                row.rationale = rationale
            _file(events, row.id, now)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "extend_fyi_cluster", write)

    def get(self, cluster_id: str, *, owner_user_id: str | None) -> FyiCluster | None:
        """Return the owner's cluster, or ``None`` when missing or someone else's."""
        with self._session("select_fyi_cluster_by_id") as session:
            row = _owned_row(session, cluster_id, owner_user_id)
            return _to_entity(row) if row is not None else None

    def list(
        self, *, owner_user_id: str | None, states: Collection[str] | None = None
    ) -> builtins.list[FyiCluster]:
        """List the owner's clusters oldest first, in every state when ``states`` is ``None``."""
        stmt = select(SqlFyiCluster).where(_owned(owner_user_id))
        if states is not None:
            stmt = stmt.where(
                SqlFyiCluster.state.in_([encode_fyi_cluster_state(state) for state in states])
            )
        stmt = stmt.order_by(asc(SqlFyiCluster.created_at), asc(SqlFyiCluster.id))
        with self._session("list_fyi_clusters") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def dismiss(self, cluster_id: str, *, owner_user_id: str | None) -> FyiCluster | None:
        """Dismiss the owner's cluster and the events filed into it."""
        now = now_epoch()

        def write(session: Session) -> FyiCluster | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, cluster_id, owner_user_id)
            if row is None:
                return None
            if row.state == _PENDING:
                row.state = _DISMISSED_CLUSTER
                row.resolved_at = now
                session.execute(
                    update(SqlTaskEvent)
                    .where(
                        SqlTaskEvent.workspace_id == current_workspace_id(),
                        SqlTaskEvent.fyi_cluster_id == row.id,
                        SqlTaskEvent.state == _FILED,
                    )
                    .values(state=_DISMISSED_EVENT, updated_at=now)
                )
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "dismiss_fyi_cluster", write)
