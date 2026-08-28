"""Drop legacy omniharness agent, rebind sessions to onih-openai-agents.

Revision ID: d1e2f3a4b5c7
Revises: a7b8c9d0e1f2
Create Date: 2026-07-14

The ``omniharness`` execution target was superseded by the Onih dual-executor
split (``onih-openai-agents`` + ``onih-pi``).  The design doc prescribed a
manual rename of the old ``omniharness`` agent row to ``onih-openai-agents``
during rollout, retaining its stable agent ID so existing sessions kept
resolving.  Instances that completed that rename no longer have a
``omniharness`` row; this migration is a no-op for them.

For instances that still have a ``omniharness`` template agent row, this
migration does one of two things per workspace:

* **Both** ``omniharness`` **and** ``onih-openai-agents`` exist — rebind any
  conversations pointing at the legacy agent to ``onih-openai-agents``, then
  delete the legacy row.
* **Only** ``omniharness`` **exists** — rename it to ``onih-openai-agents``
  (matching the manual-rollout plan), so sessions keep resolving by agent ID.

After this migration runs, no ``omniharness`` template agent row remains in
any workspace, and the ``LEGACY_OMNIHARNESS_TARGET`` filtering code in the
parser and web UI can be removed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c7"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_logger = logging.getLogger(__name__)


def upgrade() -> None:
    bind = op.get_bind()

    legacy_workspaces = bind.execute(
        sa.text(
            "SELECT DISTINCT workspace_id FROM agents "
            "WHERE name = 'omniharness' AND kind = 1"
        )
    ).fetchall()

    if not legacy_workspaces:
        return

    for (ws_id,) in legacy_workspaces:
        legacy_row = bind.execute(
            sa.text(
                "SELECT id FROM agents "
                "WHERE name = 'omniharness' AND kind = 1 AND workspace_id = :ws"
            ),
            {"ws": ws_id},
        ).fetchone()
        if legacy_row is None:
            continue
        legacy_id = legacy_row[0]

        new_row = bind.execute(
            sa.text(
                "SELECT id FROM agents "
                "WHERE name = 'onih-openai-agents' AND kind = 1 AND workspace_id = :ws"
            ),
            {"ws": ws_id},
        ).fetchone()

        if new_row is not None:
            new_id = new_row[0]
            bind.execute(
                sa.text(
                    "UPDATE conversations SET agent_id = :new_id "
                    "WHERE agent_id = :old_id"
                ),
                {"new_id": new_id, "old_id": legacy_id},
            )
            bind.execute(
                sa.text(
                    "DELETE FROM agents WHERE id = :id AND workspace_id = :ws"
                ),
                {"id": legacy_id, "ws": ws_id},
            )
            _logger.info(
                "Migrated workspace %s: rebound sessions from omniharness to "
                "onih-openai-agents, deleted legacy agent row",
                ws_id,
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE agents SET name = 'onih-openai-agents' "
                    "WHERE id = :id AND workspace_id = :ws"
                ),
                {"id": legacy_id, "ws": ws_id},
            )
            _logger.info(
                "Migrated workspace %s: renamed omniharness agent to "
                "onih-openai-agents",
                ws_id,
            )


def downgrade() -> None:
    """Not reversible.

    Reverting would require re-creating the ``omniharness`` row and un-binding
    conversations from ``onih-openai-agents``, but we cannot distinguish a
    renamed legacy row from a fresh ``onih-openai-agents`` seed.  The new code
    re-seeds ``onih-openai-agents`` on boot regardless, so a no-op is safe.
    """
