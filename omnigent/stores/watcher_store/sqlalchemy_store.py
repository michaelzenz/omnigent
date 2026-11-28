"""SQLAlchemy-backed GlobalHub watcher store."""

from __future__ import annotations

import builtins
import json
from collections.abc import Collection
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, asc, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import (
    SqlRoleSchedule,
    SqlSourceWatcher,
    SqlWatcher,
    SqlWatchItem,
    current_workspace_id,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import Watcher, WatcherProblem, WatcherProblemSource
from omnigent.stores.watcher_store import UNSET, WatcherStore


def _decode_problem(raw: str | None) -> WatcherProblem | None:
    if not raw:
        return None
    data = json.loads(raw)
    return WatcherProblem(
        reported_at=int(data["reported_at"]),
        sources=tuple(
            WatcherProblemSource(
                source=item["source"], reason=item["reason"], source_key=item.get("source_key")
            )
            for item in data.get("sources", [])
        ),
        note=data.get("note"),
        run_error=data.get("run_error"),
    )


def _encode_problem(problem: WatcherProblem | None) -> str | None:
    if problem is None:
        return None
    return json.dumps(
        {
            "reported_at": problem.reported_at,
            "sources": [
                {"source": item.source, "source_key": item.source_key, "reason": item.reason}
                for item in problem.sources
            ],
            "note": problem.note,
            "run_error": problem.run_error,
        }
    )


def _to_entity(row: SqlWatcher) -> Watcher:
    return Watcher(
        id=row.id,
        owner_user_id=row.owner_user_id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
        manual=row.manual,
        model=row.model,
        conversation_id=row.conversation_id,
        builtin=row.builtin,
        problem=_decode_problem(row.problem),
        cursor=row.cursor,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlWatcher.workspace_id == current_workspace_id(),
        SqlWatcher.owner_user_id == owner_user_id,
    )


def _owned_row(session: Session, watcher_id: str, owner_user_id: str | None) -> SqlWatcher | None:
    row = session.get(SqlWatcher, (current_workspace_id(), watcher_id))
    return row if row is not None and row.owner_user_id == owner_user_id else None


class SqlAlchemyWatcherStore(WatcherStore):
    """
    SQLAlchemy-backed implementation of :class:`WatcherStore`.

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
            query_name_prefix="omnigent.watcher_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.watcher_store",
            immediate=True,
        )

    def create(
        self,
        watcher_id: str,
        *,
        owner_user_id: str | None,
        name: str,
        description: str = "",
        manual: str | None = None,
        model: str | None = None,
        builtin: bool = False,
    ) -> Watcher:
        """Insert a watcher. Its session comes with its first run."""
        now = now_epoch()

        def write(session: Session) -> Watcher:
            require_active_account(session, owner_user_id)
            row = SqlWatcher(
                id=watcher_id,
                owner_user_id=owner_user_id,
                name=name,
                description=description,
                manual=manual,
                model=model,
                builtin=builtin,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "insert_watcher", write)

    def get(self, watcher_id: str, *, owner_user_id: str | None) -> Watcher | None:
        """Return the owner's watcher, or ``None`` when missing or someone else's."""
        with self._session("select_watcher_by_id") as session:
            row = _owned_row(session, watcher_id, owner_user_id)
            return _to_entity(row) if row is not None else None

    def get_builtin(self, *, owner_user_id: str | None) -> Watcher | None:
        """Return the owner's built-in fallback watcher, or ``None`` before it exists."""
        stmt = (
            select(SqlWatcher)
            .where(_owned(owner_user_id), SqlWatcher.builtin.is_(True))
            .order_by(asc(SqlWatcher.created_at), asc(SqlWatcher.id))
        )
        with self._session("select_builtin_watcher") as session:
            row = session.execute(stmt).scalars().first()
            return _to_entity(row) if row is not None else None

    def get_by_conversation_id(
        self, conversation_id: str, *, owner_user_id: str | None
    ) -> Watcher | None:
        """Return the owner's watcher whose session is ``conversation_id``, or ``None``."""
        stmt = select(SqlWatcher).where(
            _owned(owner_user_id), SqlWatcher.conversation_id == conversation_id
        )
        with self._session("select_watcher_by_conversation") as session:
            row = session.execute(stmt).scalars().first()
            return _to_entity(row) if row is not None else None

    def list(self, *, owner_user_id: str | None) -> builtins.list[Watcher]:
        """List the owner's watchers in creation order."""
        stmt = (
            select(SqlWatcher)
            .where(_owned(owner_user_id))
            .order_by(asc(SqlWatcher.created_at), asc(SqlWatcher.id))
        )
        with self._session("list_watchers") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def update(
        self,
        watcher_id: str,
        *,
        owner_user_id: str | None,
        name: str = UNSET,
        description: str = UNSET,
        manual: str | None = UNSET,
        model: str | None = UNSET,
        conversation_id: str | None = UNSET,
    ) -> Watcher | None:
        """Change the given fields of the owner's watcher."""
        changes: dict[str, Any] = {
            field: value
            for field, value in (
                ("name", name),
                ("description", description),
                ("manual", manual),
                ("model", model),
                ("conversation_id", conversation_id),
            )
            if value is not UNSET
        }

        def write(session: Session) -> Watcher | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, watcher_id, owner_user_id)
            if row is None:
                return None
            changed = False
            for field, value in changes.items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed = True
            if changed:
                row.updated_at = now_epoch()
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "update_watcher", write)

    def set_problem(
        self, watcher_id: str, problem: WatcherProblem | None, *, owner_user_id: str | None
    ) -> Watcher | None:
        def write(session: Session) -> Watcher | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, watcher_id, owner_user_id)
            if row is None:
                return None
            row.problem = _encode_problem(problem)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "set_watcher_problem", write)

    def set_cursor(
        self, watcher_id: str, cursor: str | None, *, owner_user_id: str | None
    ) -> Watcher | None:
        def write(session: Session) -> Watcher | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, watcher_id, owner_user_id)
            if row is None:
                return None
            row.cursor = cursor
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "save_watcher_cursor", write)

    def clear_problems(self, watcher_ids: Collection[str], *, owner_user_id: str | None) -> int:
        ids = list(dict.fromkeys(watcher_ids))
        if not ids:
            return 0

        def write(session: Session) -> int:
            require_active_account(session, owner_user_id)
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    update(SqlWatcher)
                    .where(
                        _owned(owner_user_id),
                        SqlWatcher.id.in_(ids),
                        SqlWatcher.problem.is_not(None),
                    )
                    .values(problem=None)
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount

        return run_write_transaction(self._session_immediate, "clear_watcher_problems", write)

    def settle_run_problem(
        self, conversation_id: str, *, started_at: int, error: str | None, now: int
    ) -> Watcher | None:
        stmt = select(SqlWatcher).where(
            SqlWatcher.workspace_id == current_workspace_id(),
            SqlWatcher.conversation_id == conversation_id,
        )
        with self._session("select_watcher_at_run_end") as session:
            found = session.execute(stmt).scalars().first()
            if found is None:
                return None
            if found.problem is None and error is None:
                return _to_entity(found)

        def write(session: Session) -> Watcher | None:
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            current = _decode_problem(row.problem)
            kept = current if current is not None and current.reported_at >= started_at else None
            if error is not None:
                kept = WatcherProblem(
                    reported_at=now,
                    sources=kept.sources if kept is not None else (),
                    note=kept.note if kept is not None else None,
                    run_error=error,
                )
            encoded = _encode_problem(kept)
            if row.problem != encoded:
                row.problem = encoded
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "settle_watcher_run_problem", write)

    def delete(self, watcher_id: str, *, owner_user_id: str | None) -> Watcher | None:
        """Delete the owner's watcher, its schedule, its tracked items and its source types."""

        def write(session: Session) -> Watcher | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, watcher_id, owner_user_id)
            if row is None:
                return None
            deleted = _to_entity(row)
            session.execute(
                delete(SqlRoleSchedule).where(
                    SqlRoleSchedule.workspace_id == current_workspace_id(),
                    SqlRoleSchedule.owner_user_id == owner_user_id,
                    SqlRoleSchedule.role_key == deleted.role_key,
                )
            )
            session.execute(
                delete(SqlWatchItem).where(
                    SqlWatchItem.workspace_id == current_workspace_id(),
                    SqlWatchItem.owner_user_id == owner_user_id,
                    SqlWatchItem.watcher_id == deleted.id,
                )
            )
            session.execute(
                delete(SqlSourceWatcher).where(
                    SqlSourceWatcher.workspace_id == current_workspace_id(),
                    SqlSourceWatcher.owner_user_id == owner_user_id,
                    SqlSourceWatcher.watcher_id == deleted.id,
                )
            )
            session.delete(row)
            return deleted

        return run_write_transaction(self._session_immediate, "delete_watcher", write)
