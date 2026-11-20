"""Add the staged changes that a GlobalHub system notice proposes.

Revision ID: b7d41c2e9f06
Revises: 4250a650a381

Adds ``system_notices.changes``: the changes the custodian staged and then
published as the notice, e.g. two tasks to merge. A click on the notice sends
the approved changes to the custodian. The existing notices get no changes.
Downgrade drops the column, with the changes in it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMBLOB

revision: str = "b7d41c2e9f06"
down_revision: str | None = "4250a650a381"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "system_notices"
_LARGE_COMPRESSED_TEXT = sa.LargeBinary().with_variant(MEDIUMBLOB(), "mysql")


def upgrade() -> None:
    """Add ``changes`` to ``system_notices``, empty for every notice."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column("changes", _LARGE_COMPRESSED_TEXT, nullable=True))


def downgrade() -> None:
    """Drop ``changes`` from ``system_notices``."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("changes")
