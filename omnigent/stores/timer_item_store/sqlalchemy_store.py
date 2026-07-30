"""SQLAlchemy-backed timer item store."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import asc, select, update

from omnigent.db.db_models import SqlTimerItem, current_workspace_id
from omnigent.db.enum_codecs import decode_timer_item_state, encode_timer_item_state
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import TimerItem
from omnigent.stores.timer_item_store import TimerItemStore


def _row_to_entity(row: SqlTimerItem) -> TimerItem:
    try:
        payload = json.loads(row.payload)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return TimerItem(
        id=row.id,
        task_type=row.task_type,
        fire_at=row.fire_at,
        state=decode_timer_item_state(row.state),
        host_id=row.host_id,
        payload=payload,
        owner_user_id=row.owner_user_id,
        created_at=row.created_at,
        fired_at=row.fired_at,
    )


class SqlAlchemyTimerItemStore(TimerItemStore):
    """SQLAlchemy-backed implementation of :class:`TimerItemStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    def create_item(
        self,
        item_id: str,
        task_type: str,
        fire_at: int,
        host_id: str,
        payload: dict[str, Any],
        *,
        owner_user_id: str | None = None,
    ) -> TimerItem:
        row = SqlTimerItem(
            id=item_id,
            task_type=task_type,
            fire_at=fire_at,
            state=encode_timer_item_state("pending"),
            host_id=host_id,
            payload=json.dumps(payload),
            owner_user_id=owner_user_id,
            created_at=now_epoch(),
            fired_at=None,
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            return _row_to_entity(row)

    def get_item(self, item_id: str) -> TimerItem | None:
        with self._session() as session:
            row = session.get(SqlTimerItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            return _row_to_entity(row)

    def list_due(self, host_id: str, *, now: int) -> list[TimerItem]:
        with self._session() as session:
            stmt = (
                select(SqlTimerItem)
                .where(SqlTimerItem.workspace_id == current_workspace_id())
                .where(SqlTimerItem.host_id == host_id)
                .where(SqlTimerItem.state == encode_timer_item_state("pending"))
                .where(SqlTimerItem.fire_at <= now)
                .order_by(asc(SqlTimerItem.fire_at), asc(SqlTimerItem.id))
            )
            rows = session.execute(stmt).scalars().all()
            return [_row_to_entity(row) for row in rows]

    def claim_item(self, item_id: str, host_id: str) -> TimerItem | None:
        fired_at = now_epoch()
        with self._session() as session:
            stmt = (
                update(SqlTimerItem)
                .where(SqlTimerItem.workspace_id == current_workspace_id())
                .where(SqlTimerItem.id == item_id)
                .where(SqlTimerItem.host_id == host_id)
                .where(SqlTimerItem.state == encode_timer_item_state("pending"))
                .values(
                    state=encode_timer_item_state("running"),
                    fired_at=fired_at,
                )
            )
            result = session.execute(stmt)
            if result.rowcount == 0:
                return None
            row = session.get(SqlTimerItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            return _row_to_entity(row)

    def complete_item(self, item_id: str, host_id: str) -> TimerItem | None:
        with self._session() as session:
            stmt = (
                update(SqlTimerItem)
                .where(SqlTimerItem.workspace_id == current_workspace_id())
                .where(SqlTimerItem.id == item_id)
                .where(SqlTimerItem.host_id == host_id)
                .where(SqlTimerItem.state == encode_timer_item_state("running"))
                .values(state=encode_timer_item_state("done"))
            )
            result = session.execute(stmt)
            if result.rowcount == 0:
                return None
            row = session.get(SqlTimerItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            return _row_to_entity(row)

    def fail_item(self, item_id: str, host_id: str) -> TimerItem | None:
        with self._session() as session:
            stmt = (
                update(SqlTimerItem)
                .where(SqlTimerItem.workspace_id == current_workspace_id())
                .where(SqlTimerItem.id == item_id)
                .where(SqlTimerItem.host_id == host_id)
                .where(SqlTimerItem.state == encode_timer_item_state("running"))
                .values(state=encode_timer_item_state("failed"))
            )
            result = session.execute(stmt)
            if result.rowcount == 0:
                return None
            row = session.get(SqlTimerItem, (current_workspace_id(), item_id))
            if row is None:
                return None
            return _row_to_entity(row)
