"""Tests for secretary ambiguous-event clustering."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.secretary_inbox import cluster_ambiguous_events
from omnigent.entities import EventTag
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def event_store(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


def test_cluster_ambiguous_events_groups_by_tags(event_store) -> None:
    e1 = event_store.create_event(
        _uid("e1"),
        "github.pr.checks_failed",
        "checks failed",
        state="awaiting_grouping",
        source="poll",
        source_key="org/repo#891",
        tags=[
            EventTag(tag_type="repo", tag="org/repo"),
            EventTag(tag_type="pr", tag="891"),
        ],
    )
    e2 = event_store.create_event(
        _uid("e2"),
        "github.pr.review_comment",
        "new comment",
        state="awaiting_grouping",
        source="poll",
        source_key="org/repo#891",
        tags=[
            EventTag(tag_type="repo", tag="org/repo"),
            EventTag(tag_type="pr", tag="891"),
        ],
    )
    clusters = cluster_ambiguous_events([e1, e2])
    assert len(clusters) == 1
    assert len(clusters[0].events) == 2
    assert clusters[0].tags == [
        {"tag_type": "pr", "tag": "891"},
        {"tag_type": "repo", "tag": "org/repo"},
    ]
