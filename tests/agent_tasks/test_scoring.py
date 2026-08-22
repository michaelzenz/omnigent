"""Tests for tag-overlap task routing confidence scoring."""

from __future__ import annotations

from omnigent.agent_tasks.scoring import (
    pick_auto_route,
    rank_tasks_for_event_tags,
    score_task_for_event_tags,
)
from omnigent.entities import EventTag, Task, TaskTag


def _task(task_id: str) -> Task:
    return Task(
        id=task_id,
        manager_role_key="manager:default",
        worker_role_key="worker:default",
        owner_user_id=None,
        title="title",
        description=None,
        internal_note=None,
        state="active",
        created_at=1,
        goal="goal",
    )


class _TagStore:
    def __init__(self, tags_by_task: dict[str, list[TaskTag]]) -> None:
        self._tags_by_task = tags_by_task

    def get_tags(self, task_id: str) -> list[TaskTag]:
        return self._tags_by_task.get(task_id, [])


def test_score_task_counts_tag_overlap() -> None:
    event_tags = [
        EventTag(tag_type="repo", tag="omnigent-fork"),
        EventTag(tag_type="component", tag="ci"),
    ]
    task_tags = [
        TaskTag(task_id="t1", tag_type="repo", tag="omnigent-fork"),
        TaskTag(task_id="t1", tag_type="domain", tag="build"),
    ]
    score = score_task_for_event_tags(event_tags=event_tags, task_tags=task_tags)
    assert score == 0.5


def test_pick_auto_route_requires_margin() -> None:
    ranked = [
        (_task("a"), 0.72),
        (_task("b"), 0.68),
    ]
    assert pick_auto_route(ranked) is None


def test_pick_auto_route_returns_clear_winner() -> None:
    ranked = [
        (_task("a"), 1.0),
        (_task("b"), 0.2),
    ]
    winner = pick_auto_route(ranked)
    assert winner is not None
    assert winner.id == "a"


def test_rank_tasks_orders_by_score() -> None:
    store = _TagStore(
        {
            "low": [TaskTag(task_id="low", tag_type="repo", tag="other")],
            "high": [
                TaskTag(task_id="high", tag_type="repo", tag="omnigent-fork"),
                TaskTag(task_id="high", tag_type="component", tag="ci"),
            ],
        },
    )
    event_tags = [EventTag(tag_type="repo", tag="omnigent-fork")]
    ranked = rank_tasks_for_event_tags(
        event_tags=event_tags,
        tasks=[_task("low"), _task("high")],
        task_store=store,  # type: ignore[arg-type]
    )
    assert len(ranked) == 1
    assert ranked[0][0].id == "high"
    assert ranked[0][1] == 1.0
