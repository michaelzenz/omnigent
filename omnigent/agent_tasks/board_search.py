"""Board search: rank the user's live tasks by the query words their cards show.

A query word hits a task when its card text holds the word anywhere: the
title, overview, task id, asset titles and URLs, and worker labels.
Each distinct word counts once per task, however often the
card holds it, so a query of two words hits a task at most twice. A task that
hits more words comes first; among tasks with the same count, the task active
most recently comes first. Chat content is not searched by word.

With vector search, :func:`rank_with_vectors` ranks the keyword matches and the
tasks near the query in meaning together, in one list.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from omnigent.entities import Task, TaskAsset, Worker

if TYPE_CHECKING:
    from omnigent.stores.task_embedding_store import TaskVectorScores

#: The most distinct words a query uses.
MAX_QUERY_WORDS = 20
KEYWORD_WEIGHT = 0.3
MAX_MEANING_ONLY_MATCHES = 20


def query_words(query: str) -> list[str]:
    """Return the query's distinct lowercase words, in the order they first appear."""
    return list(dict.fromkeys(query.lower().split()))[:MAX_QUERY_WORDS]


@dataclass(frozen=True)
class BoardMatch:
    """A task whose card holds a query word, or that is near the query in meaning.

    :param task: The matching task.
    :param hits: How many distinct query words the card holds.
    :param last_active: The latest of the task's last change and its workers'
        session activity, in Unix epoch seconds.
    :param asset_ids: The task's assets whose title or URL holds a query word.
    :param worker_ids: The task's workers whose label holds a query word, or
        whose session's recent turns are near the query.
    :param title_match: Whether the task's own title and overview are near the
        query in meaning.
    """

    task: Task
    hits: int
    last_active: int
    asset_ids: tuple[str, ...]
    worker_ids: tuple[str, ...]
    title_match: bool = False


def search_board(
    tasks: Sequence[Task],
    words: Sequence[str],
    *,
    assets: Sequence[TaskAsset],
    workers: Sequence[Worker],
    worker_labels: Mapping[str, str],
    worker_activity: Mapping[str, int],
) -> list[BoardMatch]:
    """
    Return the tasks whose cards hold any of ``words``, best match first.

    A task with more distinct words comes first, then the one active most
    recently, then the one higher on the board.

    :param tasks: The user's live tasks.
    :param words: Distinct lowercase query words, from :func:`query_words`.
    :param assets: The assets of those tasks.
    :param workers: The workers that the cards show.
    :param worker_labels: Each worker's label on its card, by worker id.
    :param worker_activity: When each worker's session last changed, by worker id.
    :returns: One match per task that holds a query word.
    """
    assets_by_task: defaultdict[str, list[TaskAsset]] = defaultdict(list)
    for asset in assets:
        assets_by_task[asset.task_id].append(asset)
    workers_by_task: defaultdict[str, list[Worker]] = defaultdict(list)
    for worker in workers:
        workers_by_task[worker.task_id].append(worker)
    last_active = last_active_by_task(tasks, workers, worker_activity)

    matches: list[BoardMatch] = []
    for task in tasks:
        task_assets = assets_by_task[task.id]
        task_workers = workers_by_task[task.id]
        asset_texts = {asset.id: f"{asset.title}\n{asset.url}".lower() for asset in task_assets}
        worker_texts = {
            worker.id: worker_labels.get(worker.id, "").lower() for worker in task_workers
        }
        texts = [
            *(text.lower() for text in (task.title, task.description or "", task.id)),
            *asset_texts.values(),
            *worker_texts.values(),
        ]
        hits = sum(1 for word in words if any(word in text for text in texts))
        if not hits:
            continue
        matches.append(
            BoardMatch(
                task=task,
                hits=hits,
                last_active=last_active[task.id],
                asset_ids=tuple(
                    asset_id for asset_id, text in asset_texts.items() if _holds_any(text, words)
                ),
                worker_ids=tuple(
                    worker_id
                    for worker_id, text in worker_texts.items()
                    if _holds_any(text, words)
                ),
            )
        )
    matches.sort(
        key=lambda match: (-match.hits, -match.last_active, -match.task.queue_rank, match.task.id)
    )
    return matches


def last_active_by_task(
    tasks: Sequence[Task], workers: Sequence[Worker], worker_activity: Mapping[str, int]
) -> dict[str, int]:
    """Return the latest of each task's last change and its workers' session activity."""
    latest = {task.id: task.updated_at or task.created_at for task in tasks}
    for worker in workers:
        if worker.task_id in latest:
            latest[worker.task_id] = max(latest[worker.task_id], worker_activity.get(worker.id, 0))
    return latest


def rank_with_vectors(
    tasks: Sequence[Task],
    keyword_matches: Sequence[BoardMatch],
    words: Sequence[str],
    *,
    scores: Mapping[str, TaskVectorScores],
    min_score: float,
    shown_worker_ids: Collection[str],
    last_active: Mapping[str, int],
    routed_at: Mapping[tuple[str, str], int],
) -> list[BoardMatch]:
    """
    Return the tasks that hold a query word or are near the query in meaning, best first.

    A task's vector score is the similarity of its nearest vector: its own
    title and overview, or the recent turns of the session of one of the
    workers that its card shows. A task shows when its card holds a query
    word, or when its vector score is at least ``min_score``; at most
    :data:`MAX_MEANING_ONLY_MATCHES` (20) tasks show with no query word. The
    order is by the vector score plus :data:`KEYWORD_WEIGHT` (0.3) times the
    share of the query words that the card holds. A tie goes to the task that
    the matching session's events went to last, then to the task active most
    recently.

    :param tasks: The user's live tasks.
    :param keyword_matches: The tasks whose cards hold a query word, from
        :func:`search_board`.
    :param words: The distinct query words.
    :param scores: How near each task is to the query, by task id.
    :param min_score: The least similarity that makes a match.
    :param shown_worker_ids: The workers that the cards show.
    :param last_active: Each task's last activity, by task id.
    :param routed_at: When each session's events last went to each task, by
        ``(session_id, task_id)``.
    """
    keyword_by_task = {match.task.id: match for match in keyword_matches}
    with_words: list[tuple[float, int, BoardMatch]] = []
    meaning_only: list[tuple[float, int, BoardMatch]] = []
    for task in tasks:
        keyword = keyword_by_task.get(task.id)
        task_scores = scores.get(task.id)
        title_score = task_scores.title_score if task_scores is not None else None
        workers = [
            worker
            for worker in (task_scores.workers if task_scores is not None else ())
            if worker.worker_id in shown_worker_ids
        ]
        vector_score = max(
            [worker.score for worker in workers]
            + ([title_score] if title_score is not None else []),
            default=0.0,
        )
        if keyword is None and vector_score < min_score:
            continue
        near_workers = [worker for worker in workers if worker.score >= min_score]
        match = BoardMatch(
            task=task,
            hits=keyword.hits if keyword is not None else 0,
            last_active=last_active.get(task.id, task.updated_at or task.created_at),
            asset_ids=keyword.asset_ids if keyword is not None else (),
            worker_ids=tuple(
                dict.fromkeys(
                    [
                        *(keyword.worker_ids if keyword is not None else ()),
                        *(worker.worker_id for worker in near_workers),
                    ]
                )
            ),
            title_match=title_score is not None and title_score >= min_score,
        )
        share = match.hits / len(words) if words else 0.0
        routed = max(
            (routed_at.get((worker.session_id, task.id), 0) for worker in near_workers),
            default=0,
        )
        entry = (vector_score + KEYWORD_WEIGHT * share, routed, match)
        (with_words if match.hits else meaning_only).append(entry)
    meaning_only.sort(key=_rank_key)
    ranked = [*with_words, *meaning_only[:MAX_MEANING_ONLY_MATCHES]]
    ranked.sort(key=_rank_key)
    return [match for _, _, match in ranked]


def _rank_key(entry: tuple[float, int, BoardMatch]) -> tuple[float, int, int, int, str]:
    score, routed, match = entry
    return (-score, -routed, -match.last_active, -match.task.queue_rank, match.task.id)


def _holds_any(text: str, words: Sequence[str]) -> bool:
    return any(word in text for word in words)
