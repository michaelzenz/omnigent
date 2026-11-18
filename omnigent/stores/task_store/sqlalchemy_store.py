"""SQLAlchemy-backed GlobalHub task store."""

from __future__ import annotations

import builtins
from collections.abc import Collection
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, delete, desc, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import (
    SqlTask,
    SqlTaskAsset,
    SqlWorker,
    current_workspace_id,
)
from omnigent.db.enum_codecs import decode_task_state, encode_task_state
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import Task
from omnigent.stores.task_store import UNSET, TaskStore

_RESOLVED = "agent-resolved"
_ACTIVE_CODE = encode_task_state("active")
_IDLE_CODE = encode_task_state("idle")


def _to_entity(row: SqlTask) -> Task:
    return Task(
        id=row.id,
        owner_user_id=row.owner_user_id,
        title=row.title,
        state=decode_task_state(row.state),
        created_at=row.created_at,
        manager_id=row.manager_id,
        description=row.description,
        internal_note=row.internal_note,
        priority=row.priority,
        queue_rank=row.queue_rank,
        updated_at=row.updated_at,
        resolved_at=row.resolved_at,
        has_update=row.has_update,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlTask.workspace_id == current_workspace_id(),
        SqlTask.owner_user_id == owner_user_id,
    )


def _matching(
    owner_user_id: str | None, states: Collection[str] | None, unmanaged: bool
) -> list[ColumnElement[bool]]:
    clauses = [_owned(owner_user_id)]
    if states is not None:
        clauses.append(SqlTask.state.in_([encode_task_state(state) for state in states]))
    if unmanaged:
        clauses.append(SqlTask.manager_id.is_(None))
    return clauses


def _owned_row(session: Session, task_id: str, owner_user_id: str | None) -> SqlTask | None:
    row = session.get(SqlTask, (current_workspace_id(), task_id))
    return row if row is not None and row.owner_user_id == owner_user_id else None


def _below(position: tuple[int, str]) -> ColumnElement[bool]:
    """Tasks that come after a ``(queue_rank, id)`` board position."""
    rank, task_id = position
    return and_(
        SqlTask.queue_rank <= rank,
        or_(SqlTask.queue_rank < rank, and_(SqlTask.queue_rank == rank, SqlTask.id < task_id)),
    )


def _above(position: tuple[int, str]) -> ColumnElement[bool]:
    """Tasks that come before a ``(queue_rank, id)`` board position."""
    rank, task_id = position
    return and_(
        SqlTask.queue_rank >= rank,
        or_(SqlTask.queue_rank > rank, and_(SqlTask.queue_rank == rank, SqlTask.id > task_id)),
    )


def _rank_above_all(session: Session, owner_user_id: str | None) -> int:
    top = session.scalar(select(func.max(SqlTask.queue_rank)).where(_owned(owner_user_id)))
    return int(top or 0) + 1


def _rank_below_all(session: Session, owner_user_id: str | None) -> int:
    bottom = session.scalar(select(func.min(SqlTask.queue_rank)).where(_owned(owner_user_id)))
    return int(bottom or 0) - 1


class SqlAlchemyTaskStore(TaskStore):
    """
    SQLAlchemy-backed implementation of :class:`TaskStore`.

    Every query is scoped by ``workspace_id`` and ``owner_user_id``, including
    the board-order arithmetic, so one user's moves never renumber another's.
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
            query_name_prefix="omnigent.task_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.task_store",
            immediate=True,
        )

    def create(
        self,
        task_id: str,
        *,
        owner_user_id: str | None,
        title: str,
        description: str | None = None,
        internal_note: str | None = None,
        manager_id: str | None = None,
        state: str = "active",
        priority: int = 2,
    ) -> Task:
        """Insert a task at the top of its owner's board."""
        now = now_epoch()
        encoded_state = encode_task_state(state)

        def write(session: Session) -> Task:
            require_active_account(session, owner_user_id)
            row = SqlTask(
                id=task_id,
                owner_user_id=owner_user_id,
                manager_id=manager_id,
                title=title,
                description=description,
                internal_note=internal_note,
                state=encoded_state,
                priority=priority,
                queue_rank=_rank_above_all(session, owner_user_id),
                created_at=now,
                updated_at=None,
                resolved_at=now if state == _RESOLVED else None,
            )
            session.add(row)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "insert_task", write)

    def get(self, task_id: str, *, owner_user_id: str | None) -> Task | None:
        """Return the owner's task, or ``None`` when missing or someone else's."""
        with self._session("select_task_by_id") as session:
            row = _owned_row(session, task_id, owner_user_id)
            return _to_entity(row) if row is not None else None

    def list(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        manager_id: str | None = None,
        unmanaged: bool = False,
        limit: int | None = None,
        after: tuple[int, str] | None = None,
        before: tuple[int, str] | None = None,
    ) -> builtins.list[Task]:
        """List the owner's tasks in board order (highest rank first)."""
        stmt = select(SqlTask).where(*_matching(owner_user_id, states, unmanaged))
        if manager_id is not None:
            stmt = stmt.where(SqlTask.manager_id == manager_id)
        if after is not None:
            stmt = stmt.where(_below(after))
        if before is not None:
            stmt = stmt.where(_above(before)).order_by(SqlTask.queue_rank, SqlTask.id)
        else:
            stmt = stmt.order_by(desc(SqlTask.queue_rank), desc(SqlTask.id))
        if limit is not None:
            stmt = stmt.limit(limit)
        with self._session("list_tasks") as session:
            tasks = [_to_entity(row) for row in session.execute(stmt).scalars().all()]
        return tasks[::-1] if before is not None else tasks

    def count(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        unmanaged: bool = False,
    ) -> int:
        """Count the owner's tasks."""
        stmt = (
            select(func.count())
            .select_from(SqlTask)
            .where(*_matching(owner_user_id, states, unmanaged))
        )
        with self._session("count_tasks") as session:
            return int(session.scalar(stmt) or 0)

    def update(
        self,
        task_id: str,
        *,
        owner_user_id: str | None,
        title: str | None = None,
        description: str | None = None,
        internal_note: str | None = None,
        manager_id: str | None = UNSET,
        state: str | None = None,
        priority: int | None = None,
        has_update: bool | None = None,
    ) -> Task | None:
        """Change the given fields of the owner's task; ``None`` leaves a field as is."""
        changes: dict[str, Any] = {
            field: value
            for field, value in (
                ("title", title),
                ("description", description),
                ("internal_note", internal_note),
                ("priority", priority),
            )
            if value is not None
        }
        if manager_id is not UNSET:
            changes["manager_id"] = manager_id
        encoded_state = encode_task_state(state) if state is not None else None

        def write(session: Session) -> Task | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, task_id, owner_user_id)
            if row is None:
                return None
            now = now_epoch()
            changed = False
            for field, value in changes.items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed = True
            if encoded_state is not None and row.state != encoded_state:
                row.state = encoded_state
                row.resolved_at = now if state == _RESOLVED else None
                changed = True
            if changed:
                row.updated_at = now
            if has_update is not None:
                row.has_update = has_update
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "update_task", write)

    def record_work(self, task_id: str, *, owner_user_id: str | None) -> Task | None:
        """Record work on the owner's task; a resolved or archived task stays as it is."""

        def write(session: Session) -> Task | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, task_id, owner_user_id)
            if row is None:
                return None
            if row.state in (_ACTIVE_CODE, _IDLE_CODE):
                row.state = _ACTIVE_CODE
                row.queue_rank = _rank_above_all(session, owner_user_id)
                row.updated_at = now_epoch()
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "record_task_work", write)

    def idle_quiet_tasks_all_workspaces(self, *, changed_before: int) -> int:
        """Turn idle each active task, in every workspace, unchanged since ``changed_before``."""

        def write(session: Session) -> int:
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    update(SqlTask)
                    .where(
                        SqlTask.state == _ACTIVE_CODE,
                        func.coalesce(SqlTask.updated_at, SqlTask.created_at) < changed_before,
                    )
                    .values(state=_IDLE_CODE)
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount

        return run_write_transaction(
            self._session_immediate, "idle_quiet_tasks_in_all_workspaces", write
        )

    def move_to_board_top(self, task_id: str, *, owner_user_id: str | None) -> Task | None:
        """Move the owner's task to the top of their board, or return ``None``."""

        def write(session: Session) -> Task | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, task_id, owner_user_id)
            if row is None:
                return None
            row.queue_rank = _rank_above_all(session, owner_user_id)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "move_task_to_board_top", write)

    def move_to_board_end(self, task_id: str, *, owner_user_id: str | None) -> Task | None:
        """Move the owner's task toward the end of their board.

        An ``agent-resolved`` task goes to the very end; any other task goes
        just above the first ``agent-resolved`` task.
        """
        resolved_code = encode_task_state(_RESOLVED)

        def write(session: Session) -> Task | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, task_id, owner_user_id)
            if row is None:
                return None
            resolved_top = (
                None
                if row.state == resolved_code
                else session.scalar(
                    select(func.max(SqlTask.queue_rank)).where(
                        _owned(owner_user_id), SqlTask.state == resolved_code
                    )
                )
            )
            if resolved_top is None:
                row.queue_rank = _rank_below_all(session, owner_user_id)
                return _to_entity(row)
            # Board order is rank descending, so the first resolved task holds
            # the highest resolved rank; the slot just above it is free unless
            # another task already sits there.
            next_above = session.scalar(
                select(func.min(SqlTask.queue_rank)).where(
                    _owned(owner_user_id),
                    SqlTask.queue_rank > resolved_top,
                    SqlTask.id != task_id,
                )
            )
            if next_above is None or next_above - resolved_top >= 2:
                row.queue_rank = resolved_top + 1
            else:
                # No free integer between them: shift everything at or below the
                # first resolved task down by one, keeping their order.
                session.execute(
                    update(SqlTask)
                    .where(_owned(owner_user_id), SqlTask.queue_rank <= resolved_top)
                    .values(queue_rank=SqlTask.queue_rank - 1)
                    .execution_options(synchronize_session=False)
                )
                row.queue_rank = resolved_top
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "move_task_to_board_end", write)

    def delete(self, task_id: str, *, owner_user_id: str | None) -> bool:
        """Permanently delete the owner's task, its workers and its assets.

        :returns: Whether the task existed.
        """

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, task_id, owner_user_id)
            if row is None:
                return False
            session.execute(
                delete(SqlWorker).where(
                    SqlWorker.workspace_id == current_workspace_id(),
                    SqlWorker.task_id == task_id,
                )
            )
            session.execute(
                delete(SqlTaskAsset).where(
                    SqlTaskAsset.workspace_id == current_workspace_id(),
                    SqlTaskAsset.task_id == task_id,
                )
            )
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "delete_task", write)
