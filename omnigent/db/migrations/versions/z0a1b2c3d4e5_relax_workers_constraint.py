"""Relax workers CHECK constraint for external sessions without agent.

Revision ID: z0a1b2c3d4e5
Revises: m9z8y7x6w5v4
Create Date: 2026-08-13 00:00:00.000000

Watcher-discovered external sessions may not have a known agent at adoption
time. Drops the ``agent_profile_id IS NOT NULL`` requirement from the
``ck_workers_role_or_agent`` CHECK constraint so external workers can be
created with a NULL ``agent_profile_id``.
"""

from __future__ import annotations

from alembic import op

revision: str = "z0a1b2c3d4e5"
down_revision: str | None = "m9z8y7x6w5v4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.drop_constraint("ck_workers_role_or_agent", type_="check")
        batch_op.create_check_constraint(
            "ck_workers_role_or_agent",
            "(kind = 'managed' AND role_key IS NOT NULL AND agent_profile_id IS NULL) "
            "OR (kind = 'external' AND role_key IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("workers", schema=None) as batch_op:
        batch_op.drop_constraint("ck_workers_role_or_agent", type_="check")
        batch_op.create_check_constraint(
            "ck_workers_role_or_agent",
            "(kind = 'managed' AND role_key IS NOT NULL AND agent_profile_id IS NULL) "
            "OR (kind = 'external' AND role_key IS NULL AND agent_profile_id IS NOT NULL)",
        )
