"""SQLAlchemy-backed task role definition store."""

from __future__ import annotations

from sqlalchemy import select

from omnigent.agent_tasks.agent_builtins import task_role_defaults_for_key
from omnigent.agent_tasks.constants import DEFAULT_TASK_WORKSPACE
from omnigent.agent_tasks.role_keys import role_kind_from_key
from omnigent.db.db_models import SqlTaskRoleProfile, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore


def _to_entity(row: SqlTaskRoleProfile) -> TaskRoleProfile:
    return TaskRoleProfile(
        role=row.role,
        kind=row.kind,
        agent_profile_id=row.agent_profile_id,
        harness=row.harness,
        model=row.model,
        host_id=row.host_id,
        workspace=row.workspace,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyTaskRoleProfileStore(TaskRoleProfileStore):
    """SQLAlchemy-backed implementation of :class:`TaskRoleProfileStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    def get(self, role: str) -> TaskRoleProfile | None:
        with self._session() as session:
            row = session.get(SqlTaskRoleProfile, (current_workspace_id(), role))
            if row is None:
                return None
            return _to_entity(row)

    def list_roles(self, *, kind: str | None = None) -> list[TaskRoleProfile]:
        with self._session() as session:
            stmt = select(SqlTaskRoleProfile).where(
                SqlTaskRoleProfile.workspace_id == current_workspace_id()
            )
            if kind is not None:
                stmt = stmt.where(SqlTaskRoleProfile.kind == kind)
            stmt = stmt.order_by(SqlTaskRoleProfile.role.asc())
            rows = session.execute(stmt).scalars().all()
            return [_to_entity(row) for row in rows]

    def delete(self, role: str) -> bool:
        with self._session() as session:
            row = session.get(SqlTaskRoleProfile, (current_workspace_id(), role))
            if row is None:
                return False
            session.delete(row)
            session.flush()
            return True

    def upsert(
        self,
        role: str,
        *,
        kind: str | None = None,
        agent_profile_id: str | None = None,
        harness: str | None = None,
        model: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        clear_model: bool = False,
    ) -> TaskRoleProfile:
        with self._session() as session:
            row = session.get(SqlTaskRoleProfile, (current_workspace_id(), role))
            now = now_epoch()
            if row is None:
                defaults = task_role_defaults_for_key(role)
                row = SqlTaskRoleProfile(
                    role=role,
                    kind=kind or role_kind_from_key(role),
                    agent_profile_id=agent_profile_id,
                    harness=harness or (defaults.harness if defaults else None),
                    model=(
                        None if clear_model else model or (defaults.model if defaults else None)
                    ),
                    host_id=host_id,
                    workspace=workspace or DEFAULT_TASK_WORKSPACE,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                if kind is not None:
                    row.kind = kind
                if agent_profile_id is not None:
                    row.agent_profile_id = agent_profile_id
                if harness is not None:
                    row.harness = harness
                if clear_model:
                    row.model = None
                elif model is not None:
                    row.model = model
                if host_id is not None:
                    row.host_id = host_id
                if workspace is not None:
                    row.workspace = workspace
                row.updated_at = now
            session.flush()
            return _to_entity(row)
