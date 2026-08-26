"""Add npm_registry_url column to ssh_settings for custom npm registries.

Allows users behind corporate firewalls to configure a custom npm registry
for installing harness CLIs (e.g. Pi) on remote SSH hosts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "n4rg3x3c_g3n_m3rg3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ssh_settings") as batch_op:
        batch_op.add_column(sa.Column("npm_registry_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ssh_settings") as batch_op:
        batch_op.drop_column("npm_registry_url")
