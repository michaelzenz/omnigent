"""Add first-class managers and backfill live task managers.

Revision ID: c7d8e9f0a1b3
Revises: b6d7e8f9a0c1
Create Date: 2026-09-03
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import CompressedText, Uuid16

revision: str = "c7d8e9f0a1b3"
down_revision: str | None = "b6d7e8f9a0c1"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_EVENT_STATE_AWAITING_GROUPING = 4
_EVENT_STATE_ROUTED = 6


def _concise_description(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()[:512]
    return ""


def upgrade() -> None:
    op.add_column(
        "task_events",
        sa.Column("manager_conversation_id", Uuid16(), nullable=True),
    )
    op.create_index(
        "ix_task_events_manager_state",
        "task_events",
        ["workspace_id", "manager_conversation_id", "state", "created_at", "id"],
    )
    op.create_table(
        "managers",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("owner_user_id", sa.String(128), nullable=False),
        sa.Column("role_key", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id"),
    )
    op.create_index(
        "ix_managers_owner",
        "managers",
        ["workspace_id", "owner_user_id", "created_at", "conversation_id"],
    )

    bind = op.get_bind()
    metadata = sa.MetaData()
    tasks = sa.Table(
        "tasks",
        metadata,
        sa.Column("workspace_id", sa.BigInteger()),
        sa.Column("id", Uuid16()),
        sa.Column("manager_conversation_id", Uuid16()),
        sa.Column("owner_user_id", sa.String(128)),
        sa.Column("manager_role_key", sa.String(64)),
        sa.Column("title", sa.String(256)),
        sa.Column("description", CompressedText()),
        sa.Column("goal", sa.Text()),
        sa.Column("state", sa.SmallInteger()),
        sa.Column("created_at", sa.Integer()),
        sa.Column("updated_at", sa.Integer()),
    )
    task_events = sa.Table(
        "task_events",
        metadata,
        sa.Column("workspace_id", sa.BigInteger()),
        sa.Column("id", Uuid16()),
        sa.Column("task_id", Uuid16()),
        sa.Column("manager_conversation_id", Uuid16()),
        sa.Column("owner_user_id", sa.String(128)),
        sa.Column("state", sa.SmallInteger()),
    )
    permissions = sa.Table(
        "session_permissions",
        metadata,
        sa.Column("workspace_id", sa.BigInteger()),
        sa.Column("user_id", sa.String(128)),
        sa.Column("conversation_id", Uuid16()),
        sa.Column("level", sa.Integer()),
    )
    managers = sa.Table(
        "managers",
        metadata,
        sa.Column("workspace_id", sa.BigInteger()),
        sa.Column("conversation_id", Uuid16()),
        sa.Column("owner_user_id", sa.String(128)),
        sa.Column("role_key", sa.String(64)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.Integer()),
        sa.Column("updated_at", sa.Integer()),
    )
    rows = list(
        bind.execute(
            sa.select(tasks)
            .where(
                tasks.c.manager_conversation_id.is_not(None),
                tasks.c.state.in_((1, 2, 3, 5)),
            )
            .order_by(
                tasks.c.workspace_id,
                tasks.c.manager_conversation_id,
                sa.func.coalesce(tasks.c.owner_user_id, "__anonymous__"),
                tasks.c.created_at,
                tasks.c.id,
            )
        )
        .mappings()
    )
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["workspace_id"], row["manager_conversation_id"])
        grouped.setdefault(key, []).append(dict(row))

    backfill: dict[tuple[int, str], dict[str, Any]] = {}
    for key, shared_rows in grouped.items():
        task_owners = {
            row["owner_user_id"] or "__anonymous__" for row in shared_rows
        }
        permission_owners = set(
            bind.execute(
                sa.select(permissions.c.user_id).where(
                    permissions.c.workspace_id == key[0],
                    permissions.c.conversation_id == key[1],
                    permissions.c.level == 4,
                )
            ).scalars()
        )
        if len(permission_owners) == 1:
            selected_owner = next(iter(permission_owners))
        elif not permission_owners and len(task_owners) == 1:
            selected_owner = next(iter(task_owners))
        else:
            bind.execute(
                tasks.update()
                .where(
                    tasks.c.workspace_id == key[0],
                    tasks.c.manager_conversation_id == key[1],
                )
                .values(manager_conversation_id=None)
            )
            continue
        selected_rows = [
            row
            for row in shared_rows
            if (row["owner_user_id"] or "__anonymous__") == selected_owner
        ]
        metadata_rows = selected_rows or shared_rows
        exemplar = metadata_rows[0]
        created_at = min((row["created_at"] or 0) for row in metadata_rows)
        updated_at = max(
            (row["updated_at"] or row["created_at"] or 0) for row in metadata_rows
        )
        description = next(
            (
                candidate
                for row in metadata_rows
                if (
                    candidate := _concise_description(
                        row["description"], row["goal"], row["title"]
                    )
                )
            ),
            "",
        )
        backfill[key] = {
            "workspace_id": exemplar["workspace_id"],
            "conversation_id": exemplar["manager_conversation_id"],
            "owner_user_id": selected_owner,
            "role_key": exemplar["manager_role_key"] or "manager:default",
            "description": description,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        bind.execute(
            tasks.update()
            .where(
                tasks.c.workspace_id == key[0],
                tasks.c.manager_conversation_id == key[1],
                sa.func.coalesce(tasks.c.owner_user_id, "__anonymous__")
                != selected_owner,
            )
            .values(manager_conversation_id=None)
        )
    if backfill:
        bind.execute(managers.insert(), list(backfill.values()))

    # Materialize manager routing provenance for every existing task-bound
    # event so runtime delivery never needs to derive managers from tasks.
    task_bindings = bind.execute(
        sa.select(
            tasks.c.workspace_id,
            tasks.c.id,
            tasks.c.owner_user_id,
            tasks.c.manager_conversation_id,
        ).where(tasks.c.manager_conversation_id.is_not(None))
    ).mappings()
    for binding in task_bindings:
        manager_key = (
            binding["workspace_id"],
            binding["manager_conversation_id"],
        )
        manager_row = backfill.get(manager_key)
        if manager_row is None:
            continue
        manager_owner = manager_row["owner_user_id"]
        if (binding["owner_user_id"] or "__anonymous__") != manager_owner:
            continue
        bind.execute(
            task_events.update()
            .where(
                task_events.c.workspace_id == binding["workspace_id"],
                task_events.c.task_id == binding["id"],
                task_events.c.manager_conversation_id.is_(None),
                sa.or_(
                    task_events.c.owner_user_id == manager_owner,
                    task_events.c.owner_user_id.is_(None),
                ),
            )
            .values(
                manager_conversation_id=binding["manager_conversation_id"],
                owner_user_id=manager_owner,
            )
        )

    # Any routed event that could not be tied to a migrated, owner-compatible
    # manager returns to broker routing instead of targeting a dangling session.
    bind.execute(
        task_events.update()
        .where(
            task_events.c.manager_conversation_id.is_(None),
            task_events.c.state == _EVENT_STATE_ROUTED,
        )
        .values(
            task_id=None,
            owner_user_id=sa.func.coalesce(
                task_events.c.owner_user_id,
                "__anonymous__",
            ),
            state=_EVENT_STATE_AWAITING_GROUPING,
        )
    )


def downgrade() -> None:
    op.drop_index("ix_managers_owner", table_name="managers")
    op.drop_table("managers")
    op.drop_index("ix_task_events_manager_state", table_name="task_events")
    op.drop_column("task_events", "manager_conversation_id")
