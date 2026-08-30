"""Tests for the manager task search (scorer + ranking)."""

from __future__ import annotations

import uuid

from omnigent.agent_tasks.task_search import rank_tasks_by_text, score_task_text
from omnigent.entities import Task


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _task(
    seed: str,
    *,
    title: str = "",
    goal: str = "",
    description: str | None = None,
    internal_note: str | None = None,
) -> Task:
    return Task(
        id=_uid(seed),
        manager_role_key="manager:default",
        owner_user_id="user-1",
        title=title,
        description=description,
        internal_note=internal_note,
        state="active",
        created_at=1,
        goal=goal,
    )


def test_score_prefers_title_hits() -> None:
    titled = _task("a", title="S3 upload reliability", goal="unrelated")
    noted = _task("b", title="unrelated", goal="unrelated", internal_note="S3 upload reliability")
    assert score_task_text(titled, "s3 upload") > score_task_text(noted, "s3 upload")


def test_score_normalizes_per_query_token() -> None:
    task = _task("a", title="s3")
    one_token = score_task_text(task, "s3")
    many_tokens = score_task_text(task, "s3 missing tokens here")
    assert one_token > many_tokens


def test_score_zero_without_overlap() -> None:
    assert score_task_text(_task("a", title="delta lake"), "kubernetes pods") == 0.0
    assert score_task_text(_task("a", title="delta lake"), "") == 0.0


def test_rank_tasks_by_text_orders_and_filters() -> None:
    tasks = [
        _task("best", title="s3 upload retries", goal="reliable uploads"),
        _task("ok", title="upload dashboard"),
        _task("miss", title="billing export"),
    ]
    ranked = rank_tasks_by_text(tasks, "s3 upload")
    assert [task.id for task, _ in ranked] == [_uid("best"), _uid("ok")]
    assert ranked[0][1] > ranked[1][1]


def test_rank_tasks_by_text_caps_at_limit() -> None:
    tasks = [_task(f"t{i}", title=f"s3 task {i}") for i in range(25)]
    ranked = rank_tasks_by_text(tasks, "s3 task", limit=20)
    assert len(ranked) == 20
