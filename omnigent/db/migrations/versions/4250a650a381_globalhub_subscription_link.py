"""Add the link of the sources that GlobalHub sessions follow.

Revision ID: 4250a650a381
Revises: 5ba8fed0798c

Adds ``subscriptions.url``: where a person opens a followed source whose key is
not a web address, e.g. a ticket's page. Events still match the source key. The
existing subscriptions get no link. Downgrade drops the column, with the links
in it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4250a650a381"
down_revision: str | None = "5ba8fed0798c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "subscriptions"


def upgrade() -> None:
    """Add ``url`` to ``subscriptions``, empty for every subscription."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column("url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    """Drop ``url`` from ``subscriptions``."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("url")
