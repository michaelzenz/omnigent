"""Tests for ranking board cards by the query words they hold."""

from __future__ import annotations

from typing import Any

from omnigent.agent_tasks.board_search import query_words, search_board
from omnigent.entities import Task, TaskAsset, Worker


def _task(task_id: str, title: str, **fields: Any) -> Task:
    return Task(
        id=task_id,
        owner_user_id=None,
        title=title,
        goal="",
        state="active",
        created_at=100,
        **fields,
    )


def _worker(worker_id: str, task_id: str) -> Worker:
    return Worker(
        id=worker_id,
        task_id=task_id,
        owner_user_id=None,
        session_id=f"s-{worker_id}",
        created_at=100,
    )


def _asset(asset_id: str, title: str, url: str) -> TaskAsset:
    return TaskAsset(
        id=asset_id,
        owner_user_id=None,
        task_id="t1",
        category="documents",
        title=title,
        url=url,
        created_at=100,
    )


NOTHING_ELSE: dict[str, Any] = {
    "assets": [],
    "workers": [],
    "worker_labels": {},
    "worker_activity": {},
}


def test_query_words_are_lowercase_and_distinct() -> None:
    assert query_words("  Fix  AUTH fix ") == ["fix", "auth"]


def test_more_distinct_words_rank_first_and_a_repeated_word_counts_once() -> None:
    repeats = _task("t1", "retry retry retry retry", goal="retry again")
    both = _task("t2", "Retry the auth call")
    neither = _task("t3", "Docs")

    found = search_board([repeats, both, neither], query_words("retry auth"), **NOTHING_ELSE)

    assert [(match.task.id, match.hits) for match in found] == [("t2", 2), ("t1", 1)]


def test_equal_hits_rank_the_task_active_most_recently_first() -> None:
    changed_last = _task("t1", "Fix retries", updated_at=300, queue_rank=2)
    worked_on_last = _task("t2", "Fix logs", updated_at=200, queue_rank=1)
    untouched = _task("t3", "Fix docs", queue_rank=3)

    found = search_board(
        [changed_last, worked_on_last, untouched],
        ["fix"],
        **{**NOTHING_ELSE, "workers": [_worker("w1", "t2")], "worker_activity": {"w1": 400}},
    )

    assert [(match.task.id, match.last_active) for match in found] == [
        ("t2", 400),
        ("t1", 300),
        ("t3", 100),
    ]


def test_assets_and_workers_count_and_name_their_matches() -> None:
    task = _task("t1", "Storage work", manager_id="m1")

    [match] = search_board(
        [task],
        query_words("retry fixing"),
        assets=[
            _asset("a1", "Design doc", "https://example.com/retry-plan"),
            _asset("a2", "Notes", "https://example.com/notes"),
        ],
        workers=[_worker("w1", "t1"), _worker("w2", "t1")],
        worker_labels={"w1": "Fixing logs", "w2": "Reading docs"},
        worker_activity={},
    )

    assert (match.hits, match.asset_ids, match.worker_ids) == (2, ("a1",), ("w1",))
