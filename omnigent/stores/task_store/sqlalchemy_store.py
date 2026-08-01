"""SQLAlchemy-backed managed-task store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, delete, desc, select

from omnigent.db.db_models import (
    SqlTask,
    SqlTaskSessionBinding,
    SqlTaskTag,
    current_workspace_id,
)
from omnigent.db.enum_codecs import decode_task_state, encode_task_state
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import Task, TaskTag
from omnigent.stores.task_store import TaskStore

_UNSET: Any = object()


def _tag_to_entity(row: SqlTaskTag) -> TaskTag:
    return TaskTag(task_id=row.task_id, tag_type=row.tag_type, tag=row.tag)


def _to_entity(row: SqlTask) -> Task:
    return Task(
        id=row.id,
        agent_profile_id=row.agent_profile_id,
        manager_conversation_id=row.manager_conversation_id,
        owner_user_id=row.owner_user_id,
        title=row.title,
        description=row.description,
        internal_note=row.internal_note,
        state=decode_task_state(row.state),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyTaskStore(TaskStore):
    """SQLAlchemy-backed implementation of :class:`TaskStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    def create(
        self,
        task_id: str,
        title: str,
        *,
        agent_profile_id: str,
        owner_user_id: str | None = None,
        description: str | None = None,
        internal_note: str | None = None,
        manager_conversation_id: str | None = None,
        state: str = "active",
        tags: list[TaskTag] | None = None,
    ) -> Task:
        tag_rows = tags or []
        row = SqlTask(
            id=task_id,
            agent_profile_id=agent_profile_id,
            manager_conversation_id=manager_conversation_id,
            owner_user_id=owner_user_id,
            title=title,
            description=description,
            internal_note=internal_note,
            state=encode_task_state(state),
            created_at=now_epoch(),
            updated_at=None,
        )
        with self._session() as session:
            session.add(row)
            for tag in tag_rows:
                session.add(
                    SqlTaskTag(
                        task_id=task_id,
                        tag_type=tag.tag_type,
                        tag=tag.tag,
                    )
                )
            session.flush()
            return _to_entity(row)

    def get(self, task_id: str) -> Task | None:
        with self._session() as session:
            row = session.get(SqlTask, (current_workspace_id(), task_id))
            if row is None:
                return None
            return _to_entity(row)

    def list(
        self,
        *,
        state: str | None = None,
    ) -> list[Task]:
        with self._session() as session:
            stmt = select(SqlTask).where(SqlTask.workspace_id == current_workspace_id())
            if state is not None:
                stmt = stmt.where(SqlTask.state == encode_task_state(state))
            stmt = stmt.order_by(desc(SqlTask.updated_at), desc(SqlTask.id))
            rows = session.execute(stmt).scalars().all()
            return [_to_entity(row) for row in rows]

    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        internal_note: str | None = None,
        manager_conversation_id: str | None = _UNSET,
        owner_user_id: str | None = _UNSET,
        agent_profile_id: str | None = None,
        state: str | None = None,
    ) -> Task | None:
        with self._session() as session:
            row = session.get(SqlTask, (current_workspace_id(), task_id))
            if row is None:
                return None
            changed = False
            if title is not None and row.title != title:
                row.title = title
                changed = True
            if description is not None and row.description != description:
                row.description = description
                changed = True
            if internal_note is not None and row.internal_note != internal_note:
                row.internal_note = internal_note
                changed = True
            if manager_conversation_id is not _UNSET and (
                row.manager_conversation_id != manager_conversation_id
            ):
                row.manager_conversation_id = manager_conversation_id
                changed = True
            if owner_user_id is not _UNSET and row.owner_user_id != owner_user_id:
                row.owner_user_id = owner_user_id
                changed = True
            if agent_profile_id is not None and row.agent_profile_id != agent_profile_id:
                row.agent_profile_id = agent_profile_id
                changed = True
            if state is not None:
                encoded_state = encode_task_state(state)
                if row.state != encoded_state:
                    row.state = encoded_state
                    changed = True
            if changed:
                row.updated_at = now_epoch()
            session.flush()
            return _to_entity(row)

    def delete(self, task_id: str) -> bool:
        with self._session() as session:
            row = session.get(SqlTask, (current_workspace_id(), task_id))
            if row is None:
                return False
            workspace_id = current_workspace_id()
            session.execute(
                delete(SqlTaskTag).where(
                    SqlTaskTag.workspace_id == workspace_id,
                    SqlTaskTag.task_id == task_id,
                )
            )
            session.execute(
                delete(SqlTaskSessionBinding).where(
                    SqlTaskSessionBinding.workspace_id == workspace_id,
                    SqlTaskSessionBinding.task_id == task_id,
                )
            )
            session.delete(row)
            return True

    def get_tags(self, task_id: str) -> list[TaskTag]:
        with self._session() as session:
            stmt = (
                select(SqlTaskTag)
                .where(SqlTaskTag.workspace_id == current_workspace_id())
                .where(SqlTaskTag.task_id == task_id)
                .order_by(asc(SqlTaskTag.tag_type), asc(SqlTaskTag.tag))
            )
            rows = session.execute(stmt).scalars().all()
            return [_tag_to_entity(row) for row in rows]

    def set_tags(self, task_id: str, tags: list[TaskTag]) -> list[TaskTag]:
        with self._session() as session:
            row = session.get(SqlTask, (current_workspace_id(), task_id))
            if row is None:
                raise ValueError(f"unknown task id: {task_id!r}")
            workspace_id = current_workspace_id()
            session.execute(
                delete(SqlTaskTag).where(
                    SqlTaskTag.workspace_id == workspace_id,
                    SqlTaskTag.task_id == task_id,
                )
            )
            for tag in tags:
                session.add(
                    SqlTaskTag(
                        task_id=task_id,
                        tag_type=tag.tag_type,
                        tag=tag.tag,
                    )
                )
            row.updated_at = now_epoch()
            session.flush()
            return tags

    def list_task_ids_by_tag(self, tag_type: str, tag: str) -> list[str]:
        with self._session() as session:
            stmt = (
                select(SqlTaskTag.task_id)
                .where(SqlTaskTag.workspace_id == current_workspace_id())
                .where(SqlTaskTag.tag_type == tag_type)
                .where(SqlTaskTag.tag == tag)
                .order_by(asc(SqlTaskTag.task_id))
            )
            return list(session.execute(stmt).scalars().all())
