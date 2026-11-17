"""Tests for ranking board cards by the query words they hold."""

from __future__ import annotations

from typing import Any

from omnigent.agent_tasks.board_search import query_words, rank_with_vectors, search_board
from omnigent.entities import Task, TaskAsset, Worker
from omnigent.stores.task_embedding_store import TaskVectorScores, WorkerScore


def _task(task_id: str, title: str, **fields: Any) -> Task:
    return Task(
        id=task_id,
        owner_user_id=None,
        title=title,
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
    repeats = _task("t1", "retry retry retry retry", description="retry again")
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


def test_vectors_and_words_share_one_ranking_that_marks_what_matched() -> None:
    both_words = _task("t1", "Fix the S3 redeployment")
    near_session = _task("t2", "Roll out the storage gateway again")
    one_word = _task("t3", "S3 bucket cleanup")
    near_miss = _task("t4", "Fix flaky tests")
    shared_routed_early = _task("t5", "Shared session, routed early")
    shared_routed_late = _task("t6", "Shared session, routed late")
    tasks = [
        both_words,
        near_session,
        one_word,
        near_miss,
        shared_routed_early,
        shared_routed_late,
    ]
    words = query_words("s3 redeployment")

    def scores(task_id: str, title: float | None, *workers: WorkerScore) -> TaskVectorScores:
        return TaskVectorScores(task_id=task_id, title_score=title, workers=workers)

    ranked = rank_with_vectors(
        tasks,
        search_board(tasks, words, **NOTHING_ELSE),
        words,
        scores={
            "t1": scores("t1", 0.4),
            "t2": scores("t2", 0.55, WorkerScore("w2", "s-w2", 0.8)),
            "t3": scores("t3", 0.2),
            "t4": scores("t4", 0.45, WorkerScore("w-hidden", "s-hidden", 0.99)),
            "t5": scores("t5", None, WorkerScore("w5", "s-shared", 0.6)),
            "t6": scores("t6", None, WorkerScore("w6", "s-shared", 0.6)),
        },
        min_score=0.5,
        shown_worker_ids={"w2", "w5", "w6"},
        last_active={task.id: 100 for task in tasks},
        routed_at={("s-shared", "t5"): 10, ("s-shared", "t6"): 50},
    )

    assert [
        (match.task.id, match.hits, match.title_match, match.worker_ids) for match in ranked
    ] == [
        ("t2", 0, True, ("w2",)),
        ("t1", 2, False, ()),
        ("t6", 0, False, ("w6",)),
        ("t5", 0, False, ("w5",)),
        ("t3", 1, False, ()),
    ]
