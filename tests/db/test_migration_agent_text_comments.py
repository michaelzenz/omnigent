"""Migration ``5f2c9e1a7b3d`` adds users' unsent comments on agent text.

At head a comment needs a span that ends after it starts. The downgrade drops
the table.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache


def _comment(engine: sa.Engine, start: int, end: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO agent_text_comments (conversation_id, id, owner_user_id, "
                "conversation_item_id, start_offset, end_offset, selected_text, "
                "prefix_context, suffix_context, body, created_at, updated_at) "
                "VALUES (:conversation_id, :id, 'alice@example.com', :item_id, :start, :end, "
                "'', '', '', '', 0, 0)"
            ),
            {
                "conversation_id": uuid.uuid4().bytes,
                "id": uuid.uuid4().bytes,
                "item_id": uuid.uuid4().bytes,
                "start": start,
                "end": end,
            },
        )


def test_a_comment_needs_a_span_until_the_downgrade(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "5f2c9e1a7b3d")
        _comment(engine, 0, 4)
        for start, end in ((4, 4), (-1, 3)):
            with pytest.raises(sa.exc.IntegrityError):
                _comment(engine, start, end)

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "1306afe778d8")

        assert "agent_text_comments" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
        clear_engine_cache()
