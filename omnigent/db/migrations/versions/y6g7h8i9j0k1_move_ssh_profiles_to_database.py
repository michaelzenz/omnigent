"""Move SSH profile metadata and settings into server storage."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "y6g7h8i9j0k1"
down_revision = "x5f6g7h8i9j0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ssh_host_installations") as batch_op:
        batch_op.add_column(
            sa.Column("label", sa.String(length=128), nullable=False, server_default="")
        )
    op.execute(
        sa.text(
            "UPDATE ssh_host_installations SET label = ssh_alias "
            "WHERE label IS NULL OR label = ''"
        )
    )
    with op.batch_alter_table("ssh_host_installations") as batch_op:
        batch_op.alter_column(
            "label",
            existing_type=sa.String(length=128),
            existing_nullable=False,
            server_default=None,
        )
    op.create_table(
        "ssh_settings",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("package_index_url", sa.String(length=512), nullable=True),
        sa.Column("remote_namespace", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("ssh_settings")
    with op.batch_alter_table("ssh_host_installations") as batch_op:
        batch_op.drop_column("label")
