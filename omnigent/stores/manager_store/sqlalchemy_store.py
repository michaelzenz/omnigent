"""SQLAlchemy-backed GlobalHub manager store."""

from __future__ import annotations

import builtins
from typing import Any

from sqlalchemy import ColumnElement, and_, asc, delete, select, update
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import (
    SqlManager,
    SqlSubscription,
    SqlTask,
    SqlTaskEvent,
    current_workspace_id,
)
from omnigent.db.enum_codecs import encode_task_event_state
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import Manager
from omnigent.stores.manager_store import UNSET, ManagerStore


def _to_entity(row: SqlManager) -> Manager:
    return Manager(
        id=row.id,
        owner_user_id=row.owner_user_id,
        title=row.title,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
        conversation_id=row.conversation_id,
        host_id=row.host_id,
        workspace=row.workspace,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlManager.workspace_id == current_workspace_id(),
        SqlManager.owner_user_id == owner_user_id,
    )


def _owned_row(session: Session, manager_id: str, owner_user_id: str | None) -> SqlManager | None:
    row = session.get(SqlManager, (current_workspace_id(), manager_id))
    return row if row is not None and row.owner_user_id == owner_user_id else None


class SqlAlchemyManagerStore(ManagerStore):
    """
    SQLAlchemy-backed implementation of :class:`ManagerStore`.

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
            query_name_prefix="omnigent.manager_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.manager_store",
            immediate=True,
        )

    def create(
        self,
        manager_id: str,
        *,
        owner_user_id: str | None,
        title: str,
        description: str = "",
        conversation_id: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
    ) -> Manager:
        """Insert a manager."""
        now = now_epoch()

        def write(session: Session) -> Manager:
            require_active_account(session, owner_user_id)
            row = SqlManager(
                id=manager_id,
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                title=title,
                description=description,
                host_id=host_id,
                workspace=workspace,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            return _to_entity(row)

        return run_write_transaction(self._session_immediate, "insert_manager", write)

    def get(self, manager_id: str, *, owner_user_id: str | None) -> Manager | None:
        """Return the owner's manager, or ``None`` when missing or someone else's."""
        with self._session("select_manager_by_id") as session:
            row = _owned_row(session, manager_id, owner_user_id)
            return _to_entity(row) if row is not None else None

    def get_by_conversation_id(
        self, conversation_id: str, *, owner_user_id: str | None
    ) -> Manager | None:
        """Return the owner's manager whose session is ``conversation_id``, or ``None``."""
        stmt = select(SqlManager).where(
            _owned(owner_user_id), SqlManager.conversation_id == conversation_id
        )
        with self._session("select_manager_by_conversation") as session:
            row = session.execute(stmt).scalars().first()
            return _to_entity(row) if row is not None else None

    def list(self, *, owner_user_id: str | None) -> builtins.list[Manager]:
        """List the owner's managers in creation order."""
        stmt = (
            select(SqlManager)
            .where(_owned(owner_user_id))
            .order_by(asc(SqlManager.created_at), asc(SqlManager.id))
        )
        with self._session("list_managers") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def update(
        self,
        manager_id: str,
        *,
        owner_user_id: str | None,
        title: str | None = None,
        description: str | None = None,
        conversation_id: str | None = UNSET,
        host_id: str | None = UNSET,
        workspace: str | None = UNSET,
    ) -> Manager | None:
        """Change the given fields of the owner's manager."""
        changes: dict[str, Any] = {
            field: value
            for field, value in (("title", title), ("description", description))
            if value is not None
        }
        changes.update(
            (field, value)
            for field, value in (
                ("conversation_id", conversation_id),
                ("host_id", host_id),
                ("workspace", workspace),
            )
            if value is not UNSET
        )

        def write(session: Session) -> Manager | None:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, manager_id, owner_user_id)
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

        return run_write_transaction(self._session_immediate, "update_manager", write)

    def delete(self, manager_id: str, *, owner_user_id: str | None) -> bool:
        """Delete the owner's manager; return whether it existed.

        Its tasks are unassigned, its session follows no source any more, and the
        unhandled events routed to its session wait for the broker again.
        """
        now = now_epoch()

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            row = _owned_row(session, manager_id, owner_user_id)
            if row is None:
                return False
            session.execute(
                update(SqlTask)
                .where(
                    SqlTask.workspace_id == current_workspace_id(),
                    SqlTask.manager_id == manager_id,
                )
                .values(manager_id=None)
            )
            if row.conversation_id is not None:
                session.execute(
                    update(SqlTaskEvent)
                    .where(
                        SqlTaskEvent.workspace_id == current_workspace_id(),
                        SqlTaskEvent.target_session_id == row.conversation_id,
                        SqlTaskEvent.state == encode_task_event_state("routed"),
                    )
                    .values(
                        state=encode_task_event_state("unrouted"),
                        target_session_id=None,
                        queue_item_id=None,
                        delivery_count=0,
                        routed_at=None,
                        updated_at=now,
                    )
                )
                session.execute(
                    delete(SqlSubscription).where(
                        SqlSubscription.workspace_id == current_workspace_id(),
                        SqlSubscription.session_id == row.conversation_id,
                    )
                )
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "delete_manager", write)
