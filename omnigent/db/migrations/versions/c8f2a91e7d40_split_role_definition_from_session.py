"""split role definitions from their live sessions

Revision ID: c8f2a91e7d40
Revises: a7c3e91d5b28
Create Date: 2026-08-07 17:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f2a91e7d40"
down_revision: str | None = "a7c3e91d5b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKER_ROLE_OR_AGENT = (
    "(kind = 'managed' AND role_key IS NOT NULL AND agent_profile_id IS NULL) "
    "OR (kind = 'external' AND role_key IS NULL AND agent_profile_id IS NOT NULL)"
)


def upgrade() -> None:
    from omnigent.db.db_models import Uuid16

    op.create_table(
        "task_role_profiles",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("agent_profile_id", Uuid16(), nullable=True),
        sa.Column("harness", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("host_id", sa.String(length=64), nullable=True),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('manager', 'worker', 'broker', 'secretary', 'external')",
            name="ck_task_role_profiles_kind",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "role"),
    )
    op.create_index(
        "ix_task_role_profiles_kind",
        "task_role_profiles",
        ["workspace_id", "kind", "role"],
    )

    op.create_table(
        "user_role_sessions",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "role"),
    )
    op.create_index(
        "ix_user_role_sessions_conversation",
        "user_role_sessions",
        ["workspace_id", "conversation_id"],
    )

    # A role key was previously stored once per user. Collapse to one shared
    # definition, keeping the earliest row so a re-run is deterministic.
    op.execute(
        sa.text(
            "INSERT INTO task_role_profiles "
            "(workspace_id, role, kind, agent_profile_id, harness, model, host_id, "
            "workspace, created_at, updated_at) "
            "SELECT workspace_id, role, "
            "CASE WHEN role LIKE 'manager:%' THEN 'manager' "
            "WHEN role LIKE 'worker:%' THEN 'worker' ELSE role END, "
            "MIN(agent_profile_id), MIN(harness), MIN(model), MIN(host_id), "
            "MIN(workspace), MIN(created_at), MAX(updated_at) "
            "FROM user_task_role_profiles "
            "WHERE role IN ('broker', 'secretary') "
            "OR role LIKE 'manager:%' OR role LIKE 'worker:%' "
            "GROUP BY workspace_id, role"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO user_role_sessions "
            "(workspace_id, user_id, role, conversation_id, created_at, updated_at) "
            "SELECT workspace_id, user_id, role, conversation_id, created_at, updated_at "
            "FROM user_task_role_profiles WHERE conversation_id IS NOT NULL"
        )
    )
    op.drop_index("ix_user_task_role_profiles_conversation", table_name="user_task_role_profiles")
    op.drop_table("user_task_role_profiles")

    op.drop_index("ix_tasks_agent_profile_id", table_name="tasks")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("agent_profile_id")
    op.create_index(
        "ix_tasks_manager_role_key",
        "tasks",
        ["workspace_id", "manager_role_key", "id"],
    )

    op.drop_index("ix_workers_profile", table_name="workers")
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.alter_column(
            "profile_id",
            new_column_name="agent_profile_id",
            existing_type=sa.LargeBinary(16),
            nullable=True,
        )
        batch_op.add_column(sa.Column("role_key", sa.String(length=64), nullable=True))
    # Managed lanes resolve their agent through the role; only adopted sessions
    # keep an agent id, since they were never spawned from one.
    op.execute(
        sa.text(
            "UPDATE workers SET role_key = COALESCE((SELECT tasks.worker_role_key FROM tasks "
            "WHERE tasks.id = workers.task_id AND tasks.workspace_id = workers.workspace_id), "
            "'worker:default'), agent_profile_id = NULL WHERE kind = 'managed'"
        )
    )
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.create_check_constraint("ck_workers_role_or_agent", _WORKER_ROLE_OR_AGENT)
    op.create_index("ix_workers_role_key", "workers", ["workspace_id", "role_key", "task_id"])


def downgrade() -> None:
    from omnigent.db.db_models import Uuid16

    op.drop_index("ix_workers_role_key", table_name="workers")
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.drop_constraint("ck_workers_role_or_agent", type_="check")
    op.execute(
        sa.text(
            "UPDATE workers SET agent_profile_id = '' "
            "WHERE agent_profile_id IS NULL AND kind = 'managed'"
        )
    )
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.drop_column("role_key")
        batch_op.alter_column(
            "agent_profile_id",
            new_column_name="profile_id",
            existing_type=sa.LargeBinary(16),
            nullable=False,
        )
    op.create_index("ix_workers_profile", "workers", ["workspace_id", "profile_id", "task_id"])

    op.drop_index("ix_tasks_manager_role_key", table_name="tasks")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "agent_profile_id",
                Uuid16(),
                nullable=False,
                server_default="",
            )
        )
    op.create_index(
        "ix_tasks_agent_profile_id",
        "tasks",
        ["workspace_id", "agent_profile_id", "id"],
    )

    op.create_table(
        "user_task_role_profiles",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("agent_profile_id", Uuid16(), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=True),
        sa.Column("harness", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("host_id", sa.String(length=64), nullable=True),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "role"),
    )
    op.create_index(
        "ix_user_task_role_profiles_conversation",
        "user_task_role_profiles",
        ["workspace_id", "conversation_id"],
    )
    op.drop_index("ix_user_role_sessions_conversation", table_name="user_role_sessions")
    op.drop_table("user_role_sessions")
    op.drop_index("ix_task_role_profiles_kind", table_name="task_role_profiles")
    op.drop_table("task_role_profiles")
