"""One-shot migration: strip the removed ``omnigent.auto_worktree*`` labels.

The managed-worktree lease model (folders + per-session leases with a
fencing seq) replaced the label-based marker: every host session on a
git folder earns its lease at bind time, so the labels carry no state.
Run once against the server database after upgrading:

    uv run python scripts/migrate_strip_auto_worktree_labels.py [DB_URI_OR_PATH]

Accepts a SQLAlchemy database URI (any backend, e.g.
``postgresql://...``) or a path to a SQLite file (defaults to
``~/.omnigent/chat.db``). Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, delete, select

from omnigent.db.db_models import ConversationLabel

_LABEL_PREFIX = "omnigent.auto_worktree"


def _engine_from_arg(arg: str) -> object:
    """Build an engine from a DB URI, or a path to a SQLite file."""
    if "://" in arg:
        return create_engine(arg)
    path = Path(arg)
    if not path.exists():
        raise SystemExit(f"database not found: {path}")
    return create_engine(f"sqlite:///{path}")


def main() -> int:
    default = Path.home() / ".omnigent" / "chat.db"
    engine = _engine_from_arg(sys.argv[1] if len(sys.argv) > 1 else str(default))
    with engine.begin() as conn:  # type: ignore[attr-defined]
        matching = (
            conn.execute(
                select(ConversationLabel.key).where(
                    ConversationLabel.key.like(f"{_LABEL_PREFIX}%")
                )
            )
            .scalars()
            .all()
        )
        if not matching:
            print(f"no {_LABEL_PREFIX}* labels found")
            return 0
        conn.execute(
            delete(ConversationLabel).where(ConversationLabel.key.like(f"{_LABEL_PREFIX}%"))
        )
    print(f"stripped {len(matching)} {_LABEL_PREFIX}* label row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
