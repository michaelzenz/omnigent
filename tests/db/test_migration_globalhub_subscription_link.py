"""Tests for the migration that adds the link of followed sources (4250a650a381).

Upgrading gives the existing subscriptions no link; downgrading drops the column.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

_PREVIOUS = "5ba8fed0798c"
_REVISION = "4250a650a381"
_SUBSCRIPTION_ID = bytes.fromhex("0123456789abcdef0123456789abcdef")
_SESSION_ID = bytes.fromhex("fedcba9876543210fedcba9876543210")


def _migrate(uri: str, engine: sa.Engine, step: str, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        getattr(command, step)(config, revision)


def _columns(engine: sa.Engine) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns("subscriptions")}


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[str, sa.Engine]]:
    uri = f"sqlite:///{tmp_path / 'subscriptions.db'}"
    engine = sa.create_engine(uri)
    try:
        yield uri, engine
    finally:
        engine.dispose()
        clear_engine_cache()


def test_upgrade_gives_no_existing_subscription_a_link_and_downgrade_drops_the_links(
    database: tuple[str, sa.Engine],
) -> None:
    uri, engine = database
    _migrate(uri, engine, "upgrade", _PREVIOUS)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO subscriptions (workspace_id, id, session_id, source_type, "
                "source_key, created_at) VALUES (0, :id, :session, 'jira.issue', 'PROJ-12', 1)"
            ),
            {"id": _SUBSCRIPTION_ID, "session": _SESSION_ID},
        )

    _migrate(uri, engine, "upgrade", _REVISION)

    with engine.connect() as conn:
        link = conn.execute(sa.text("SELECT url FROM subscriptions")).scalar_one()
    assert link is None

    _migrate(uri, engine, "downgrade", _PREVIOUS)

    assert "url" not in _columns(engine)
