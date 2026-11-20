"""The changes that the GlobalHub custodian stages, checks and publishes as one notice.

The custodian stages one change at a time, e.g. two tasks to merge, then
checks the draft and commits it. The commit replaces the active system notice
with a notice that lists the changes, one toggle each. A click sends the
approved changes back to the custodian, which makes them. A notice holds task
changes or session changes, never both, so the session changes see the tasks
after the task changes ran.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.roles import session_role
from omnigent.entities import LIVE_TASK_STATES
from omnigent.stores import ConversationStore

MAX_NOTICE_CHANGES = 10
APPLY_CHANGES_ACTION_ID = "apply_changes"
TASK_CHANGE_KINDS = frozenset({"merge_tasks", "split_task"})
SESSION_CHANGE_KINDS = frozenset({"merge_sessions", "delete_session"})
_KIND_ORDER = ("merge_tasks", "split_task", "merge_sessions", "delete_session")
_KIND_HEADINGS = {
    "merge_tasks": "Merge tasks",
    "split_task": "Split tasks",
    "merge_sessions": "Merge sessions",
    "delete_session": "Delete sessions",
}
_SUBJECT_KEY_MAX_CHARS = 32
_MARKDOWN_SPECIAL = "\\`*_[]<>"
_INTRO = (
    "Switch on each change that you approve, then click **Apply selected changes**. "
    "The custodian makes only the changes that you switch on."
)


def change_id(change: Mapping[str, Any]) -> str:
    """
    Return the id of the toggle that approves a change: its kind and its subject.

    The subject is the task or session that the change keeps, splits or deletes,
    so staging a change for the same subject again replaces the staged one.

    :param change: A staged change, e.g. ``{"kind": "merge_tasks", "keep": "t1", ...}``.
    :returns: An id of at most 48 characters, e.g. ``"merge_tasks__t1"``.
    """
    subject = str(change.get("keep") or change.get("task") or change.get("session") or "")
    if len(subject) > _SUBJECT_KEY_MAX_CHARS:
        subject = hashlib.sha256(subject.encode()).hexdigest()[:_SUBJECT_KEY_MAX_CHARS]
    return f"{change['kind']}__{subject}"


def stage_change(
    staged: Sequence[Mapping[str, Any]], change: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """
    Return the draft with a change added, or put in place of the staged change with its id.

    :param staged: The staged changes, oldest first, each with its ``id``.
    :param change: The new change, without an ``id``.
    :returns: The new draft.
    """
    record = {"id": change_id(change), **change}
    draft = [
        record if existing.get("id") == record["id"] else dict(existing) for existing in staged
    ]
    if all(existing.get("id") != record["id"] for existing in staged):
        draft.append(record)
    return draft


def check_changes(
    changes: Sequence[Mapping[str, Any]],
    *,
    owner_user_id: str | None,
    board: BoardStores,
    conversation_store: ConversationStore,
) -> list[str]:
    """
    Return what keeps the staged changes from going out as one notice. Blocking.

    A change may name only the owner's live tasks and the owner's sessions that
    are no GlobalHub role session. No change may name what another change
    removes, and one notice holds task changes or session changes, not both.

    :param changes: The staged changes, each with its ``id``.
    :param owner_user_id: The owner, or ``None`` in single-user mode.
    :param board: Holds the tasks and their workers.
    :param conversation_store: Holds the sessions and their owners.
    :returns: One sentence per problem; empty when the changes can go out.
    """
    problems: list[str] = []
    if len(changes) > MAX_NOTICE_CHANGES:
        problems.append(
            f"The draft holds {len(changes)} changes; a notice holds at most {MAX_NOTICE_CHANGES}."
        )
    kinds = {change.get("kind") for change in changes}
    if kinds & TASK_CHANGE_KINDS and kinds & SESSION_CHANGE_KINDS:
        problems.append(
            "A notice holds task changes or session changes, not both. Commit the task "
            "changes first, and stage the session changes after you made the task changes."
        )

    def task_problem(change_ref: str, task_id: str) -> str | None:
        task = board.tasks.get(task_id, owner_user_id=owner_user_id)
        if task is None or task.state not in LIVE_TASK_STATES:
            return f"{change_ref}: task {task_id} is not one of your live tasks."
        return None

    def session_problem(change_ref: str, session_id: str) -> str | None:
        session = conversation_store.get_conversation(session_id)
        if session is None or (
            owner_user_id is not None
            and conversation_store.get_session_owner(session_id, owner_only=True) != owner_user_id
        ):
            return f"{change_ref}: session {session_id} is not one of your sessions."
        if session_role(session.labels) is not None:
            return f"{change_ref}: session {session_id} is a GlobalHub role session."
        return None

    for change in changes:
        problems.extend(
            _change_problems(
                change,
                task_problem=task_problem,
                session_problem=session_problem,
                board=board,
                owner_user_id=owner_user_id,
            )
        )
    problems.extend(_conflicts(changes))
    return list(dict.fromkeys(problems))


def compose_notice(
    changes: Sequence[Mapping[str, Any]],
    *,
    owner_user_id: str | None,
    board: BoardStores,
    conversation_store: ConversationStore,
    title: str | None = None,
) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Write the notice that proposes the changes. Blocking.

    :param changes: Checked changes, each with its ``id``.
    :param owner_user_id: The owner, or ``None`` in single-user mode.
    :param board: Holds the tasks, for their titles.
    :param conversation_store: Holds the sessions, for their titles.
    :param title: The notice's headline; ``None`` writes one from the changes.
    :returns: The title, the markdown body, the one action whose toggles
        approve the changes, and the changes in the order the notice lists them.
    """
    ordered = sorted(
        (dict(change) for change in changes), key=lambda change: _KIND_ORDER.index(change["kind"])
    )

    def task_name(task_id: str) -> str:
        task = board.tasks.get(task_id, owner_user_id=owner_user_id)
        return f"_{_escape(task.title if task else task_id)}_"

    def session_link(session_id: str) -> str:
        session = conversation_store.get_conversation(session_id)
        name = _escape((session.title if session else None) or "Untitled session")
        return f"[{name}](/c/{session_id})"

    sections = [_INTRO]
    for kind in _KIND_ORDER:
        members = [change for change in ordered if change["kind"] == kind]
        if not members:
            continue
        lines = [f"### {_KIND_HEADINGS[kind]}"]
        for change in members:
            line = _change_line(change, task_name=task_name, session_link=session_link)
            lines.append(f"- **{_escape(change['summary'])}** {line}")
            if change.get("details"):
                lines.append("  " + "\n  ".join(str(change["details"]).splitlines()))
        sections.append("\n".join(lines))
    action = {
        "id": APPLY_CHANGES_ACTION_ID,
        "label": "Apply selected changes",
        "inputs": [
            {
                "id": change["id"],
                "kind": "toggle",
                "label": change["summary"],
                "group": _KIND_HEADINGS[change["kind"]],
                "default": False,
            }
            for change in ordered
        ],
    }
    return title or _default_title(ordered), "\n\n".join(sections), [action], ordered


def _change_problems(
    change: Mapping[str, Any],
    *,
    task_problem: Callable[[str, str], str | None],
    session_problem: Callable[[str, str], str | None],
    board: BoardStores,
    owner_user_id: str | None,
) -> list[str]:
    ref = str(change.get("id"))
    kind = change.get("kind")
    if kind in ("merge_tasks", "merge_sessions"):
        keep = change["keep"]
        merged = list(change["merge"])
        problems = [f"{ref}: it keeps and merges {keep}."] if keep in merged else []
        if len(set(merged)) != len(merged):
            problems.append(f"{ref}: it merges the same item twice.")
        if kind == "merge_sessions" and owner_user_id is None:
            problems.append(f"{ref}: merging sessions needs a signed-in user on this server.")
        check = task_problem if kind == "merge_tasks" else session_problem
        found = (check(ref, item_id) for item_id in dict.fromkeys([keep, *merged]))
        return [*problems, *(problem for problem in found if problem)]
    if kind == "split_task":
        task_id = change["task"]
        missing = task_problem(ref, task_id)
        if missing:
            return [missing]
        task = board.tasks.get(task_id, owner_user_id=owner_user_id)
        if task is not None and task.manager_id is None:
            return [f"{ref}: task {task_id} has no manager, and a split task needs one."]
        return [
            f"{ref}: session {session_id} does not work on task {task_id}."
            for session_id in change.get("sessions", [])
            if board.workers.get_for_task_and_session(
                task_id, session_id, owner_user_id=owner_user_id
            )
            is None
        ]
    if kind == "delete_session":
        missing = session_problem(ref, change["session"])
        return [missing] if missing else []
    return [f"{ref}: unknown kind {kind!r}."]


def _removed_ids(change: Mapping[str, Any]) -> list[str]:
    if change.get("kind") in ("merge_tasks", "merge_sessions"):
        return list(change.get("merge", []))
    if change.get("kind") == "delete_session":
        return [change["session"]]
    return []


def _named_ids(change: Mapping[str, Any]) -> set[str]:
    subjects = [change.get(field) for field in ("keep", "task", "session")]
    return {
        *(subject for subject in subjects if isinstance(subject, str)),
        *change.get("merge", []),
        *change.get("sessions", []),
    }


def _conflicts(changes: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        f"{change.get('id')}: it removes {removed}, which {other.get('id')} also names."
        for change in changes
        for removed in _removed_ids(change)
        for other in changes
        if other is not change and removed in _named_ids(other)
    ]


def _change_line(
    change: Mapping[str, Any],
    *,
    task_name: Callable[[str], str],
    session_link: Callable[[str], str],
) -> str:
    kind = change["kind"]
    if kind == "merge_tasks":
        merged = ", ".join(task_name(task_id) for task_id in change["merge"])
        return f"Keep {task_name(change['keep'])}; merge in and delete {merged}."
    if kind == "split_task":
        sessions = ", ".join(session_link(session_id) for session_id in change.get("sessions", []))
        moved = f", with {sessions}" if sessions else ""
        return (
            f"From {task_name(change['task'])}, start the task _{_escape(change['new_title'])}_"
            f"{moved}."
        )
    if kind == "merge_sessions":
        merged = ", ".join(session_link(session_id) for session_id in change["merge"])
        return f"Keep {session_link(change['keep'])}; merge in and delete {merged}."
    return f"Delete {session_link(change['session'])}."


def _default_title(changes: Sequence[Mapping[str, Any]]) -> str:
    noun = "task" if all(change["kind"] in TASK_CHANGE_KINDS for change in changes) else "session"
    count = len(changes)
    return f"{count} {noun} change{'' if count == 1 else 's'} to review"


def _escape(text: str) -> str:
    flat = " ".join(text.split())
    return "".join(f"\\{char}" if char in _MARKDOWN_SPECIAL else char for char in flat)
