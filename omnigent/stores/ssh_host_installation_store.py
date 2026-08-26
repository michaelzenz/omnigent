"""Durable state store for server-managed SSH host installations."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Engine, and_, or_, select, update
from sqlalchemy.engine import CursorResult

from omnigent.db.db_models import SqlSshHostInstallation, SqlSshSettings, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_managed_session_maker, now_epoch
from omnigent.entities import SshConnectionProfile, SshSettings


@dataclass(frozen=True)
class SshHostInstallation:
    connection_id: str
    label: str
    ssh_alias: str
    host_id: str
    owner: str
    desired_state: str
    phase: str
    generation: int
    bundle_version: str
    attempt: int
    next_attempt_at: int | None
    last_error: str | None
    lease_owner: str | None
    lease_expires_at: int | None
    created_at: int
    updated_at: int


def _entity(row: SqlSshHostInstallation) -> SshHostInstallation:
    return SshHostInstallation(
        connection_id=row.connection_id,
        label=row.label,
        ssh_alias=row.ssh_alias,
        host_id=row.host_id,
        owner=row.owner,
        desired_state=row.desired_state,
        phase=row.phase,
        generation=row.generation,
        bundle_version=row.bundle_version,
        attempt=row.attempt,
        next_attempt_at=row.next_attempt_at,
        last_error=row.last_error,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SshHostInstallationStore:
    """SQLAlchemy-backed lifecycle state and CAS leases."""

    def __init__(self, storage_location: str) -> None:
        self._engine: Engine = get_or_create_engine(storage_location)
        self._session = make_managed_session_maker(self._engine, immediate=True)

    def profiles(self) -> list[SshConnectionProfile]:
        """Return active SSH profiles in creation order."""
        with self._session() as session:
            rows = (
                session.execute(
                    select(SqlSshHostInstallation)
                    .where(
                        SqlSshHostInstallation.workspace_id == current_workspace_id(),
                        SqlSshHostInstallation.desired_state == "connected",
                    )
                    .order_by(
                        SqlSshHostInstallation.created_at, SqlSshHostInstallation.connection_id
                    )
                )
                .scalars()
                .all()
            )
            return [
                SshConnectionProfile(
                    id=row.connection_id,
                    label=row.label,
                    alias=row.ssh_alias,
                    created_at=datetime.fromtimestamp(row.created_at, UTC).isoformat(),
                    owner=row.owner,
                )
                for row in rows
            ]

    def get_settings(self) -> SshSettings:
        """Return workspace SSH settings, creating their stable namespace."""
        with self._session() as session:
            workspace_id = current_workspace_id()
            row = session.get(SqlSshSettings, workspace_id)
            if row is None:
                row = SqlSshSettings(
                    workspace_id=workspace_id,
                    package_index_url=None,
                    remote_namespace=secrets.token_hex(6),
                    updated_at=now_epoch(),
                )
                session.add(row)
            return SshSettings(
                package_index_url=row.package_index_url,
                npm_registry_url=row.npm_registry_url,
                remote_namespace=row.remote_namespace,
            )

    def update_settings(
        self,
        *,
        package_index_url: str | None,
        npm_registry_url: str | None,
        updated_by: str | None = None,
    ) -> SshSettings:
        """Persist workspace SSH settings without changing their namespace."""
        with self._session() as session:
            workspace_id = current_workspace_id()
            row = session.execute(
                select(SqlSshSettings)
                .where(SqlSshSettings.workspace_id == workspace_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                row = SqlSshSettings(
                    workspace_id=workspace_id,
                    remote_namespace=secrets.token_hex(6),
                    updated_at=now_epoch(),
                )
                session.add(row)
            row.package_index_url = package_index_url
            row.npm_registry_url = npm_registry_url
            row.updated_at = now_epoch()
            row.updated_by = updated_by
            return SshSettings(
                package_index_url=row.package_index_url,
                npm_registry_url=row.npm_registry_url,
                remote_namespace=row.remote_namespace,
            )

    def sync_connections(
        self,
        profiles: dict[str, SshConnectionProfile],
        *,
        bundle_version: str,
        owner: str,
    ) -> None:
        """Create missing rows and detach rows no longer in config."""
        now = now_epoch()
        with self._session() as session:
            rows = (
                session.execute(
                    select(SqlSshHostInstallation).where(
                        SqlSshHostInstallation.workspace_id == current_workspace_id(),
                    )
                )
                .scalars()
                .all()
            )
            existing = {row.connection_id: row for row in rows}
            for connection_id, profile in profiles.items():
                profile_owner = profile.owner or owner
                try:
                    profile_created_at = int(
                        datetime.fromisoformat(profile.created_at).timestamp()
                    )
                except ValueError:
                    profile_created_at = now
                row = existing.get(connection_id)
                if row is None:
                    session.add(
                        SqlSshHostInstallation(
                            connection_id=connection_id,
                            label=profile.label,
                            ssh_alias=profile.alias,
                            host_id=uuid.uuid4().hex,
                            owner=profile_owner,
                            desired_state="connected",
                            phase="queued",
                            generation=0,
                            bundle_version=bundle_version,
                            attempt=0,
                            next_attempt_at=now,
                            created_at=profile_created_at,
                            updated_at=now,
                        )
                    )
                else:
                    changed = False
                    if row.owner != profile_owner:
                        row.owner = profile_owner
                        changed = True
                    if row.label != profile.label:
                        row.label = profile.label
                        changed = True
                    if row.created_at != profile_created_at:
                        row.created_at = profile_created_at
                        changed = True
                    if row.ssh_alias != profile.alias:
                        row.ssh_alias = profile.alias
                        changed = True
                    if row.desired_state != "connected":
                        row.desired_state = "connected"
                        changed = True
                    if row.bundle_version != bundle_version:
                        row.bundle_version = bundle_version
                        changed = True
                    if changed:
                        row.phase = "queued"
                        row.generation += 1
                        row.next_attempt_at = now
                        row.last_error = None
                        row.updated_at = now
            for connection_id, row in existing.items():
                if connection_id not in profiles and row.desired_state != "detached":
                    row.desired_state = "detached"
                    row.phase = "detaching"
                    row.generation += 1
                    row.next_attempt_at = now
                    row.updated_at = now

    def list_candidates(self, *, now: int | None = None) -> list[SshHostInstallation]:
        """List due rows whose lease is absent or expired."""
        current = now_epoch() if now is None else now
        with self._session() as session:
            rows = session.execute(
                select(SqlSshHostInstallation)
                .where(
                    SqlSshHostInstallation.workspace_id == current_workspace_id(),
                    or_(
                        SqlSshHostInstallation.desired_state == "connected",
                        and_(
                            SqlSshHostInstallation.desired_state == "detached",
                            SqlSshHostInstallation.phase == "detaching",
                        ),
                    ),
                    or_(
                        SqlSshHostInstallation.lease_expires_at.is_(None),
                        SqlSshHostInstallation.lease_expires_at <= current,
                    ),
                    or_(
                        SqlSshHostInstallation.next_attempt_at.is_(None),
                        SqlSshHostInstallation.next_attempt_at <= current,
                    ),
                )
                .order_by(SqlSshHostInstallation.updated_at)
            ).scalars()
            return [_entity(row) for row in rows]

    def acquire(
        self,
        connection_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
        now: int | None = None,
    ) -> SshHostInstallation | None:
        """Acquire an expired lease with one compare-and-swap update."""
        current = now_epoch() if now is None else now
        workspace_id = current_workspace_id()
        with self._session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(SqlSshHostInstallation)
                    .where(
                        SqlSshHostInstallation.workspace_id == workspace_id,
                        SqlSshHostInstallation.connection_id == connection_id,
                        or_(
                            SqlSshHostInstallation.lease_expires_at.is_(None),
                            SqlSshHostInstallation.lease_expires_at <= current,
                            SqlSshHostInstallation.lease_owner == lease_owner,
                        ),
                    )
                    .values(
                        lease_owner=lease_owner,
                        lease_expires_at=current + lease_seconds,
                        updated_at=current,
                    ),
                ),
            )
            if result.rowcount != 1:
                return None
            row = session.get(SqlSshHostInstallation, (workspace_id, connection_id))
            return _entity(row) if row is not None else None

    def set_phase(
        self,
        connection_id: str,
        *,
        lease_owner: str,
        generation: int,
        phase: str,
        next_attempt_at: int | None = None,
        last_error: str | None = None,
        increment_attempt: bool = False,
        release: bool = False,
    ) -> bool:
        """Persist a phase transition only for the current lease holder."""
        now = now_epoch()
        values: dict[str, object] = {
            "phase": phase,
            "next_attempt_at": next_attempt_at,
            "last_error": last_error,
            "updated_at": now,
        }
        if increment_attempt:
            values["attempt"] = SqlSshHostInstallation.attempt + 1
        if release:
            values["lease_owner"] = None
            values["lease_expires_at"] = None
        with self._session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(SqlSshHostInstallation)
                    .where(
                        SqlSshHostInstallation.workspace_id == current_workspace_id(),
                        SqlSshHostInstallation.connection_id == connection_id,
                        SqlSshHostInstallation.lease_owner == lease_owner,
                        SqlSshHostInstallation.generation == generation,
                    )
                    .values(**values),
                ),
            )
            return result.rowcount == 1

    def release_lease(self, connection_id: str, *, lease_owner: str) -> bool:
        """Release a lease after durable intent supersedes the active worker."""
        with self._session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(SqlSshHostInstallation)
                    .where(
                        SqlSshHostInstallation.workspace_id == current_workspace_id(),
                        SqlSshHostInstallation.connection_id == connection_id,
                        SqlSshHostInstallation.lease_owner == lease_owner,
                    )
                    .values(lease_owner=None, lease_expires_at=None),
                ),
            )
            return result.rowcount == 1

    def renew_lease(
        self,
        connection_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        """Extend a live worker's lease while a slow remote step runs."""
        now = now_epoch()
        with self._session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(SqlSshHostInstallation)
                    .where(
                        SqlSshHostInstallation.workspace_id == current_workspace_id(),
                        SqlSshHostInstallation.connection_id == connection_id,
                        SqlSshHostInstallation.lease_owner == lease_owner,
                    )
                    .values(lease_expires_at=now + lease_seconds),
                ),
            )
            return result.rowcount == 1

    def retry_now(self, connection_id: str) -> bool:
        """Clear backoff and queue an immediate retry."""
        now = now_epoch()
        with self._session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(SqlSshHostInstallation)
                    .where(
                        SqlSshHostInstallation.workspace_id == current_workspace_id(),
                        SqlSshHostInstallation.connection_id == connection_id,
                        SqlSshHostInstallation.desired_state == "connected",
                    )
                    .values(
                        phase="queued",
                        generation=SqlSshHostInstallation.generation + 1,
                        next_attempt_at=now,
                        last_error=None,
                        updated_at=now,
                    ),
                ),
            )
            return result.rowcount == 1

    def snapshots(self) -> dict[str, SshHostInstallation]:
        """Return every installation in the current workspace, keyed by id."""
        with self._session() as session:
            rows = session.execute(
                select(SqlSshHostInstallation).where(
                    SqlSshHostInstallation.workspace_id == current_workspace_id(),
                )
            ).scalars()
            return {row.connection_id: _entity(row) for row in rows}
