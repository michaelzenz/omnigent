"""Task search for managers: recent tasks + text matches + tag matches.

The manager's task-selection tool (`GET /v1/agent-tasks/search`): three lists
so the LLM decides which task an event belongs to — deterministic code
produces candidates, the manager judges.
"""

from __future__ import annotations

import re

from omnigent.entities import Task

# How many recently-touched tasks the search returns.
SEARCH_RECENT_LIMIT = 3
# Cap for the ranked lists (text matches, tag matches).
SEARCH_MATCH_LIMIT = 20

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def score_task_text(task: Task, query: str) -> float:
    """Weighted token overlap between the query and one task's text fields.

    Title hits weigh most, then goal/description, then internal_note. The
    score is normalized per query token so scores stay comparable across
    query lengths.
    """
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0
    fields = (
        (task.title, 3.0),
        (task.goal, 2.0),
        (task.description or "", 2.0),
        (task.internal_note or "", 1.0),
    )
    score = 0.0
    for text, weight in fields:
        score += weight * len(query_tokens & set(_tokens(text)))
    return score / len(query_tokens)


def rank_tasks_by_text(
    tasks: list[Task],
    query: str,
    *,
    limit: int = SEARCH_MATCH_LIMIT,
) -> list[tuple[Task, float]]:
    """Return tasks with a positive text score, best first, capped at limit."""
    scored = [(task, score_task_text(task, query)) for task in tasks]
    scored = [row for row in scored if row[1] > 0]
    scored.sort(key=lambda row: (-row[1], row[0].id))
    return scored[:limit]
