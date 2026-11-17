"""add the GlobalHub task embeddings table

Revision ID: 5d8e1f3a7c20
Revises: 9b3f6d2a8c41
Create Date: 2026-11-17 00:00:00.000000

Adds ``task_embeddings``: the vectors of the GlobalHub board search, one for
the title and overview of each task, and one for the recent turns of each
session that works on a task. ``ix_task_embeddings_owner`` finds the owner's
vectors for a search. Ownership is stamped on the row; there are no
foreign-key constraints (Rule R032).

On Postgres, the migration also enables pgvector and adds an ``embedding
vector(1024)`` column, so the database finds the nearest tasks. When the
extension is not available, or the role cannot create it, the table keeps only
``embedding_bytes`` and the server scores the vectors in Python.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "5d8e1f3a7c20"
down_revision: str | None = "9b3f6d2a8c41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_logger = logging.getLogger(__name__)
_DIMENSIONS = 1024


def upgrade() -> None:
    """Create ``task_embeddings`` and its index, with a vector column where pgvector exists."""
    op.create_table(
        "task_embeddings",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("source_id", Uuid16(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("owner_user_id", sa.String(128), nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("source_changed_at", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "source_id"),
    )
    op.create_index(
        "ix_task_embeddings_owner",
        "task_embeddings",
        ["workspace_id", "owner_user_id"],
        unique=False,
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            bind.execute(
                sa.text(f"ALTER TABLE task_embeddings ADD COLUMN embedding vector({_DIMENSIONS})")
            )
    except sa.exc.DBAPIError as exc:
        _logger.warning(
            "pgvector is not available (%s); the board search scores vectors in Python",
            type(exc.orig).__name__,
        )


def downgrade() -> None:
    """Drop ``task_embeddings`` and its index; pgvector stays enabled."""
    op.drop_index("ix_task_embeddings_owner", table_name="task_embeddings")
    op.drop_table("task_embeddings")
