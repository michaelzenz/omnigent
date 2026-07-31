"""Tests for task routing confidence scoring."""

from __future__ import annotations

from omnigent.agent_tasks.scoring import (
    pick_auto_route,
    rank_tasks_for_event,
    score_task_for_event,
    tokenize_search_text,
)
from omnigent.entities import Task, TaskTag


def _task(task_id: str, search_text: str) -> Task:
    return Task(
        id=task_id,
        manager_agent_id="a" * 32,
        owner_user_id=None,
        title="title",
        description=None,
        internal_note=None,
        search_text=search_text,
        state="active",
        created_at=1,
    )


def test_tokenize_includes_tag_tokens() -> None:
    tokens = tokenize_search_text("build.finished\nrepo:omnigent-fork")
    assert "build" in tokens
    assert "finished" in tokens
    assert "repo:omnigent-fork" in tokens


def test_score_task_counts_tag_hits() -> None:
    event_tokens = tokenize_search_text("build finished repo:omnigent-fork")
    task = _task("t1", "Upload retries\nrepo:omnigent-fork\ncomponent:ci")
    score = score_task_for_event(event_tokens=event_tokens, task=task)
    assert score >= 0.5


def test_pick_auto_route_requires_margin() -> None:
    ranked = [
        (_task("a", "alpha beta"), 0.72),
        (_task("b", "alpha gamma"), 0.68),
    ]
    assert pick_auto_route(ranked) is None


def test_pick_auto_route_returns_clear_winner() -> None:
    ranked = [
        (_task("a", "alpha beta gamma"), 0.9),
        (_task("b", "delta"), 0.2),
    ]
    winner = pick_auto_route(ranked)
    assert winner is not None
    assert winner.id == "a"


def test_rank_tasks_orders_by_score() -> None:
    tasks = [
        _task("low", "unrelated"),
        _task("high", "build failed upload retries"),
    ]
    ranked = rank_tasks_for_event(
        event_search_text="build.finished\nupload retries",
        tasks=tasks,
    )
    assert ranked[0][0].id == "high"
    assert ranked[0][1] >= ranked[1][1]
