"""SQLAlchemy-backed task role profile store."""

from __future__ import annotations

from typing import Any

from omnigent.agent_tasks.agent_builtins import TASK_ROLE_DEFAULTS
from omnigent.agent_tasks.constants import DEFAULT_TASK_WORKSPACE
from omnigent.db.db_models import SqlUserTaskRoleProfile, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities.task_role_profile import UserTaskRoleProfile
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore

_UNSET: Any = object()


def _to_entity(row: SqlUserTaskRoleProfile) -> UserTaskRoleProfile:
    return UserTaskRoleProfile(
        user_id=row.user_id,
        role=row.role,
        agent_profile_id=row.agent_profile_id,
        harness=row.harness,
        model=row.model,
        conversation_id=row.conversation_id,
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

    def get(self, user_id: str, role: str) -> UserTaskRoleProfile | None:
        with self._session() as session:
            row = session.get(
                SqlUserTaskRoleProfile,
                (current_workspace_id(), user_id, role),
            )
            if row is None:
                return None
            return _to_entity(row)

    def upsert(
        self,
        user_id: str,
        role: str,
        *,
        agent_profile_id: str | None = None,
        conversation_id: str | None = None,
        harness: str | None = None,
        model: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        clear_conversation_id: bool = False,
    ) -> UserTaskRoleProfile:
        with self._session() as session:
            row = session.get(
                SqlUserTaskRoleProfile,
                (current_workspace_id(), user_id, role),
            )
            now = now_epoch()
            if row is None:
                defaults = TASK_ROLE_DEFAULTS.get(role)
                if agent_profile_id is None:
                    raise ValueError(
                        "agent_profile_id is required when creating a task role profile"
                    )
                row = SqlUserTaskRoleProfile(
                    user_id=user_id,
                    role=role,
                    agent_profile_id=agent_profile_id,
                    harness=harness or (defaults.harness if defaults else "claude-native"),
                    model=model or (defaults.model if defaults else "sonnet"),
                    conversation_id=conversation_id,
                    host_id=host_id,
                    workspace=workspace or DEFAULT_TASK_WORKSPACE,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                if agent_profile_id is not None:
                    row.agent_profile_id = agent_profile_id
                if harness is not None:
                    row.harness = harness
                if model is not None:
                    row.model = model
                if host_id is not None:
                    row.host_id = host_id
                if workspace is not None:
                    row.workspace = workspace
                if clear_conversation_id:
                    row.conversation_id = None
                elif conversation_id is not None:
                    row.conversation_id = conversation_id
                row.updated_at = now
            session.flush()
            return _to_entity(row)
