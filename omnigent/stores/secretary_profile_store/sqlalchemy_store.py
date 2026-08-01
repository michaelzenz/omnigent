"""SQLAlchemy-backed secretary profile store."""

from __future__ import annotations

from typing import Any

from omnigent.agent_tasks.constants import (
    DEFAULT_SECRETARY_HARNESS,
    DEFAULT_SECRETARY_MODEL,
    DEFAULT_TASK_WORKSPACE,
)
from omnigent.db.db_models import SqlUserSecretaryProfile, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.stores.secretary_profile_store import SecretaryProfileStore

_UNSET: Any = object()


def _to_entity(row: SqlUserSecretaryProfile) -> UserSecretaryProfile:
    return UserSecretaryProfile(
        user_id=row.user_id,
        agent_profile_id=row.agent_profile_id,
        harness=row.harness,
        model=row.model,
        conversation_id=row.conversation_id,
        host_id=row.host_id,
        workspace=row.workspace,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemySecretaryProfileStore(SecretaryProfileStore):
    """SQLAlchemy-backed implementation of :class:`SecretaryProfileStore`."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine)

    def get(self, user_id: str) -> UserSecretaryProfile | None:
        with self._session() as session:
            row = session.get(SqlUserSecretaryProfile, (current_workspace_id(), user_id))
            if row is None:
                return None
            return _to_entity(row)

    def upsert(
        self,
        user_id: str,
        *,
        agent_profile_id: str | None = None,
        conversation_id: str | None = None,
        harness: str | None = None,
        model: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        clear_conversation_id: bool = False,
    ) -> UserSecretaryProfile:
        with self._session() as session:
            row = session.get(SqlUserSecretaryProfile, (current_workspace_id(), user_id))
            now = now_epoch()
            if row is None:
                if agent_profile_id is None:
                    raise ValueError(
                        "agent_profile_id is required when creating a secretary profile"
                    )
                row = SqlUserSecretaryProfile(
                    user_id=user_id,
                    agent_profile_id=agent_profile_id,
                    harness=harness or DEFAULT_SECRETARY_HARNESS,
                    model=model or DEFAULT_SECRETARY_MODEL,
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
