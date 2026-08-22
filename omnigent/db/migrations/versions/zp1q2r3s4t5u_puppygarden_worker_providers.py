"""Add hidden role profiles and PuppyGarden Worker Providers.

PuppyGarden had no production data at rollout time, so this migration
intentionally clears its operational and legacy role-definition rows instead
of carrying forward the old prompt-bearing worker-role model.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision = "zp1q2r3s4t5u"
down_revision = "y6g7h8i9j0k1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("prompt_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true())
        )

    op.create_table(
        "worker_providers",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("configuration", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("built_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("kind IN ('internal', 'external')", name="ck_worker_providers_kind"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_worker_providers_kind",
        "worker_providers",
        ["workspace_id", "kind", "id"],
    )

    op.execute(sa.text("DELETE FROM task_role_profiles"))
    with op.batch_alter_table("task_role_profiles") as batch_op:
        batch_op.drop_constraint("ck_task_role_profiles_kind", type_="check")
        batch_op.add_column(sa.Column("prompt_profile_id", Uuid16(), nullable=True))
        batch_op.create_check_constraint(
            "ck_task_role_profiles_kind",
            "kind IN ('manager', 'broker', 'secretary', 'external')",
        )
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("worker_role_key")

    op.drop_index("ix_workers_session", table_name="workers")
    op.drop_index("ix_workers_external_hint", table_name="workers")
    op.drop_index("ix_workers_role_key", table_name="workers")
    with op.batch_alter_table("workers") as batch_op:
        batch_op.drop_constraint("ck_workers_role_or_agent", type_="check")
        batch_op.drop_column("role_key")
        batch_op.drop_column("agent_profile_id")
        batch_op.drop_column("session_id")
        batch_op.drop_column("external_session_hint")
        batch_op.add_column(sa.Column("target_id", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "state", sa.String(length=32), nullable=False, server_default="uninitialized"
            )
        )
        batch_op.add_column(
            sa.Column("needs_response", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("provider_name", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("provider_configuration", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("failure_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("last_observed_at", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_workers_state",
            "state IN ('uninitialized', 'initializing', 'idle', 'busy', "
            "'disconnected', 'initialization_failed', 'terminated')",
        )
    op.create_index("ix_workers_target", "workers", ["workspace_id", "target_id"])

    # The old worker-role/session records are deliberately not migrated.
    op.execute(sa.text("UPDATE task_events SET task_id = NULL"))
    for table in (
        "agent_queue_items",
        "agent_queues",
        "task_event_executions",
        "task_item_events",
        "task_items",
        "workers",
        "task_assets",
        "task_tags",
        "task_event_routing_attempts",
        "tasks",
        "user_role_sessions",
    ):
        op.execute(sa.text(f"DELETE FROM {table}"))
    op.execute(sa.text("DELETE FROM agents WHERE is_role = true"))


def downgrade() -> None:
    op.drop_index("ix_workers_target", table_name="workers")
    with op.batch_alter_table("workers") as batch_op:
        batch_op.drop_constraint("ck_workers_state", type_="check")
        batch_op.drop_column("last_observed_at")
        batch_op.drop_column("failure_reason")
        batch_op.drop_column("provider_configuration")
        batch_op.drop_column("provider_name")
        batch_op.drop_column("needs_response")
        batch_op.drop_column("state")
        batch_op.drop_column("target_id")
        batch_op.add_column(sa.Column("session_id", Uuid16(), nullable=True))
        batch_op.add_column(
            sa.Column("external_session_hint", sa.String(length=256), nullable=True)
        )
        batch_op.add_column(sa.Column("role_key", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("agent_profile_id", Uuid16(), nullable=True))
        batch_op.create_check_constraint(
            "ck_workers_role_or_agent",
            "(kind = 'managed' AND role_key IS NOT NULL AND agent_profile_id IS NULL) "
            "OR (kind = 'external' AND role_key IS NULL)",
        )
    op.create_index("ix_workers_role_key", "workers", ["workspace_id", "role_key", "task_id"])
    op.create_index("ix_workers_session", "workers", ["workspace_id", "session_id"])
    op.create_index(
        "ix_workers_external_hint",
        "workers",
        ["workspace_id", "external_session_hint"],
    )
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "worker_role_key",
                sa.String(length=64),
                nullable=False,
                server_default="worker:default",
            )
        )
    with op.batch_alter_table("task_role_profiles") as batch_op:
        batch_op.drop_constraint("ck_task_role_profiles_kind", type_="check")
        batch_op.drop_column("prompt_profile_id")
        batch_op.create_check_constraint(
            "ck_task_role_profiles_kind",
            "kind IN ('manager', 'worker', 'broker', 'secretary', 'external')",
        )
    op.drop_index("ix_worker_providers_kind", table_name="worker_providers")
    op.drop_table("worker_providers")
    with op.batch_alter_table("prompt_profiles") as batch_op:
        batch_op.drop_column("visible")
