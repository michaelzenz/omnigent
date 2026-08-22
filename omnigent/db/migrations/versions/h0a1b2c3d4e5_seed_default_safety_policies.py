"""Seed the default global safety policies.

Revision ID: h0a1b2c3d4e5
Revises: g0a1b2c3d4e5
Create Date: 2026-08-16 00:00:00.000000

Fresh installations should start with the standard safety baseline enabled.
The inserts are name-aware so upgrading an installation that already defines
one of these policies preserves the operator's existing row.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h0a1b2c3d4e5"
down_revision: str | None = "g0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATED_AT = 1_786_909_000
_POLICIES: tuple[tuple[str, str, str, dict[str, object] | None], ...] = (
    (
        "1c923571ceb954058339b2d5a57b5ad1",
        "deny_pii_in_llm_requests",
        "omnigent.policies.builtins.safety.deny_pii_in_llm_request",
        {"pii_types": ["secret", "API_KEY"]},
    ),
    (
        "bce1617f3d2a536c9123d61ff4288f45",
        "detect_tool_call_retry_loops",
        "omnigent.policies.builtins.safety.detect_loop",
        None,
    ),
    (
        "478179884f4e5ee4b2eaec46a6008dfb",
        "detect_agent_thrashing",
        "omnigent.policies.builtins.context.detect_thrashing",
        None,
    ),
    (
        "93a9d67a1ab75262ba8bdb26f71dbba6",
        "dangerous_actions_intent_classifier",
        "omnigent.policies.builtins.routing.dangerous_actions_intent_classifier",
        None,
    ),
)


def upgrade() -> None:
    """Insert any missing baseline policy without replacing existing rows."""
    bind = op.get_bind()
    existing_names = set(
        bind.execute(
            sa.text("SELECT name FROM policies WHERE workspace_id = 0 AND scope = 1")
        ).scalars()
    )
    insert = sa.text(
        "INSERT INTO policies "
        "(workspace_id, id, name, name_cksum, session_id, created_at, "
        "updated_at, type, handler, factory_params, enabled, scope, created_by) "
        "VALUES "
        "(0, :id, :name, :name_cksum, NULL, :created_at, NULL, 1, "
        ":handler, :factory_params, :enabled, 1, :created_by)"
    )
    for policy_id, name, handler, factory_params in _POLICIES:
        if name in existing_names:
            continue
        bind.execute(
            insert,
            {
                "id": bytes.fromhex(policy_id),
                "name": name,
                "name_cksum": hashlib.sha256(name.encode()).digest(),
                "created_at": _CREATED_AT,
                "handler": handler.encode(),
                "factory_params": (
                    json.dumps(factory_params, separators=(",", ":")).encode()
                    if factory_params is not None
                    else None
                ),
                "enabled": True,
                "created_by": "builtin",
            },
        )


def downgrade() -> None:
    """Remove rows inserted by this migration."""
    bind = op.get_bind()
    delete = sa.text("DELETE FROM policies WHERE workspace_id = 0 AND scope = 1 AND id = :id")
    for policy_id, _name, _handler, _factory_params in _POLICIES:
        bind.execute(delete, {"id": bytes.fromhex(policy_id)})
