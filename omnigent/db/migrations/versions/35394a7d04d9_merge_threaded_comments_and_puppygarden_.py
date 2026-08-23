"""merge threaded comments and puppygarden heads

Revision ID: 35394a7d04d9
Revises: 2538bf5223e7, ad2c3d4e5f60
Create Date: 2026-08-23 11:53:34.568067
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '35394a7d04d9'
down_revision: Union[str, None] = ('2538bf5223e7', 'ad2c3d4e5f60')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
