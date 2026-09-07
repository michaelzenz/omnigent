"""Scope ssh_settings per user — per-user SSH configuration.

SSH attach execution moves from the server to the user's own host daemon, so
connection profiles and their install settings (package index, npm registry,
remote namespace) become per-user. This migration rebuilds ``ssh_settings``
with a ``(workspace_id, user_id)`` primary key and explicitly backfills one
row per user that already has SSH state: every distinct profile owner plus the
last settings editor. Rows copy the previous workspace-wide values verbatim so
every existing user keeps their ``remote_namespace`` and never sees a surprise
re-install on their remote hosts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "zzb1c2d3e4f5"
down_revision = "zza0b1c2d3e4"
branch_labels = None
depends_on = None

_SETTINGS_COLUMNS = (
    "workspace_id",
    "user_id",
    "package_index_url",
    "npm_registry_url",
    "remote_namespace",
    "updated_at",
    "updated_by",
)

# Pre-migration rows have no user_id column yet.
_LEGACY_SETTINGS_COLUMNS = (
    "workspace_id",
    "package_index_url",
    "npm_registry_url",
    "remote_namespace",
    "updated_at",
    "updated_by",
)


def _upgrade() -> None:
    conn = op.get_bind()

    old_rows = [
        dict(zip(_LEGACY_SETTINGS_COLUMNS, row, strict=False))
        for row in conn.execute(
            sa.text(
                "SELECT workspace_id, package_index_url, npm_registry_url, "
                "remote_namespace, updated_at, updated_by FROM ssh_settings"
            )
        )
    ]
    owners = conn.execute(
        sa.text("SELECT DISTINCT workspace_id, owner FROM ssh_host_installations")
    ).all()

    op.rename_table("ssh_settings", "ssh_settings_legacy")
    op.create_table(
        "ssh_settings",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("package_index_url", sa.String(length=512), nullable=True),
        sa.Column("npm_registry_url", sa.String(length=512), nullable=True),
        sa.Column("remote_namespace", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )

    covered: set[tuple[int, str]] = set()

    def _insert(
        workspace_id: int,
        user_id: str,
        package_index_url: str | None,
        npm_registry_url: str | None,
        remote_namespace: str,
        updated_at: int,
        updated_by: str | None,
    ) -> None:
        key = (workspace_id, user_id)
        if key in covered:
            return
        covered.add(key)
        conn.execute(
            sa.text(
                "INSERT INTO ssh_settings (workspace_id, user_id, package_index_url, "
                "npm_registry_url, remote_namespace, updated_at, updated_by) "
                "VALUES (:workspace_id, :user_id, :package_index_url, :npm_registry_url, "
                ":remote_namespace, :updated_at, :updated_by)"
            ),
            {
                "workspace_id": workspace_id,
                "user_id": user_id,
                "package_index_url": package_index_url,
                "npm_registry_url": npm_registry_url,
                "remote_namespace": remote_namespace,
                "updated_at": updated_at,
                "updated_by": updated_by,
            },
        )

    # 1. Every user with configured SSH connections inherits the workspace
    #    settings (their remote namespace especially).
    for row in old_rows:
        workspace_id = row["workspace_id"]
        for owner_workspace, owner in owners:
            if owner_workspace != workspace_id:
                continue
            _insert(
                workspace_id,
                owner,
                row["package_index_url"],
                row["npm_registry_url"],
                row["remote_namespace"],
                row["updated_at"],
                row["updated_by"],
            )
        # 2. The last settings editor keeps a row even with no connections.
        if row["updated_by"]:
            _insert(
                workspace_id,
                row["updated_by"],
                row["package_index_url"],
                row["npm_registry_url"],
                row["remote_namespace"],
                row["updated_at"],
                row["updated_by"],
            )

    op.drop_table("ssh_settings_legacy")
    op.create_index(
        "ix_ssh_host_installations_owner",
        "ssh_host_installations",
        ["workspace_id", "owner", "desired_state"],
    )


def _downgrade() -> None:
    conn = op.get_bind()

    rows = [
        dict(zip(_SETTINGS_COLUMNS, row, strict=False))
        for row in conn.execute(
            sa.text(
                "SELECT workspace_id, user_id, package_index_url, npm_registry_url, "
                "remote_namespace, updated_at, updated_by FROM ssh_settings"
            )
        )
    ]

    op.rename_table("ssh_settings", "ssh_settings_per_user")
    op.create_table(
        "ssh_settings",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("package_index_url", sa.String(length=512), nullable=True),
        sa.Column("npm_registry_url", sa.String(length=512), nullable=True),
        sa.Column("remote_namespace", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    per_workspace: dict[int, dict[str, object]] = {}
    for row in rows:
        current = per_workspace.get(row["workspace_id"])
        if current is None or row["user_id"] == row["updated_by"]:
            per_workspace[row["workspace_id"]] = row
    for workspace_id, row in per_workspace.items():
        conn.execute(
            sa.text(
                "INSERT INTO ssh_settings (workspace_id, package_index_url, "
                "npm_registry_url, remote_namespace, updated_at, updated_by) "
                "VALUES (:workspace_id, :package_index_url, :npm_registry_url, "
                ":remote_namespace, :updated_at, :updated_by)"
            ),
            {
                "workspace_id": workspace_id,
                "package_index_url": row["package_index_url"],
                "npm_registry_url": row["npm_registry_url"],
                "remote_namespace": row["remote_namespace"],
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
            },
        )
    op.drop_table("ssh_settings_per_user")
    op.drop_index("ix_ssh_host_installations_owner", table_name="ssh_host_installations")


upgrade = _upgrade
downgrade = _downgrade
