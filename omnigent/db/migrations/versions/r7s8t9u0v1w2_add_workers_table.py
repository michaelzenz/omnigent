"""add workers table and task item worker_id

Revision ID: r7s8t9u0v1w2
Revises: q6r7s8t9u0v1
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r7s8t9u0v1w2"
down_revision: str | None = "q6r7s8t9u0v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from omnigent.db.db_models import Uuid16

    op.create_table(
        "workers",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("task_id", Uuid16(), nullable=False),
        sa.Column("profile_id", Uuid16(), nullable=False),
        sa.Column("session_id", Uuid16(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_workers_task",
        "workers",
        ["workspace_id", "task_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_workers_profile",
        "workers",
        ["workspace_id", "profile_id", "task_id"],
        unique=False,
    )

    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("worker_id", Uuid16(), nullable=True))
    op.create_index(
        "ix_task_items_worker",
        "task_items",
        ["workspace_id", "worker_id", "id"],
        unique=False,
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT workspace_id, id, task_id, worker_agent_id, created_at "
            "FROM task_items WHERE worker_agent_id IS NOT NULL"
        )
    ).fetchall()
    for workspace_id, item_id, task_id, profile_id, created_at in rows:
        worker_id = uuid.uuid4().hex
        conn.execute(
            sa.text(
                "INSERT INTO workers "
                "(workspace_id, id, task_id, profile_id, session_id, created_at, updated_at) "
                "VALUES (:workspace_id, :id, :task_id, :profile_id, NULL, :created_at, NULL)"
            ),
            {
                "workspace_id": workspace_id,
                "id": bytes.fromhex(worker_id),
                "task_id": task_id,
                "profile_id": profile_id,
                "created_at": created_at,
            },
        )
        conn.execute(
            sa.text(
                "UPDATE task_items SET worker_id = :worker_id "
                "WHERE workspace_id = :workspace_id AND id = :item_id"
            ),
            {
                "workspace_id": workspace_id,
                "worker_id": bytes.fromhex(worker_id),
                "item_id": item_id,
            },
        )

    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.drop_column("worker_agent_id")
        batch_op.drop_column("host_id")
        batch_op.drop_column("workspace")
        batch_op.drop_column("priority")


def downgrade() -> None:
    from omnigent.db.db_models import Uuid16

    with op.batch_alter_table("task_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        )
        batch_op.add_column(sa.Column("workspace", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("host_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("worker_agent_id", Uuid16(), nullable=True))
        batch_op.drop_column("worker_id")

    op.drop_index("ix_task_items_worker", table_name="task_items")
    op.drop_index("ix_workers_profile", table_name="workers")
    op.drop_index("ix_workers_task", table_name="workers")
    op.drop_table("workers")
