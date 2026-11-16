"""Per-turn instructions and context for GlobalHub role sessions.

The server attaches a role session's manual to every turn as per-request
instructions, and a manager's task roster as turn context, so neither is
stored in the session's history.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from omnigent.agent_tasks.board_stores import BoardStores, open_board_stores
from omnigent.agent_tasks.roles import (
    MANAGER_ROLE_KEY,
    WATCHER_ROLE,
    BuiltinRole,
    role_definition,
)
from omnigent.entities import LIVE_TASK_STATES, Task
from omnigent.stores.user_settings_store import open_user_settings_store

_MANAGER_IDS = "Your session id is {session_id}. Your manager id is {manager_id}."
_WATCHER_IDS = "You are the watcher {name!r}. Your watcher id is {watcher_id}."
# About 200,000 tokens, at about four characters to a token.
_ROSTER_MAX_CHARS = 800_000


def role_turn_instructions(
    role: BuiltinRole,
    session_id: str,
    conversation_store: Any,
    request_instructions: object,
) -> str:
    """Return a role session's instructions for one turn. Blocking.

    :param role: The session's role, from
        :func:`omnigent.agent_tasks.roles.session_role`.
    :param session_id: The role session, e.g. ``"conv_abc123"``.
    :param conversation_store: The server's conversation store. It answers the
        session's owner and locates the owner's settings and board.
    :param request_instructions: Instructions the forwarded request already
        carries, e.g. ``None``; kept last when non-blank.
    :returns: The owner's manual for the role (their override, else the
        packaged default), then a manager's own ids, then any request
        instructions. A watcher's manual is its own, else the packaged watcher
        template, followed by its name and id.
    """
    parts = _owner_parts(role, session_id, conversation_store)
    if isinstance(request_instructions, str) and request_instructions.strip():
        parts.append(request_instructions)
    return "\n\n".join(parts)


def role_turn_context(role: BuiltinRole, session_id: str, conversation_store: Any) -> str | None:
    """Return what a role session's model sees on one turn only, or ``None``. Blocking.

    A manager gets its ``[Task roster]``: its live tasks in board order, each
    with its internal note. The harness adds the roster to the turn's message
    for each model call and does not store it, so each turn has a current one.

    :param role: The session's role, from
        :func:`omnigent.agent_tasks.roles.session_role`.
    :param session_id: The role session, e.g. ``"conv_abc123"``.
    :param conversation_store: The server's conversation store. It answers the
        session's owner and locates the owner's board.
    :returns: The roster for a manager's session; ``None`` for other roles and
        for the session of a deleted manager.
    """
    if role.key != MANAGER_ROLE_KEY:
        return None
    board = open_board_stores(conversation_store)
    if board is None:
        return None
    owner = conversation_store.get_session_owner(session_id, owner_only=True)
    manager = board.managers.get_by_conversation_id(session_id, owner_user_id=owner)
    if manager is None:
        return None
    tasks = board.tasks.list(owner_user_id=owner, states=LIVE_TASK_STATES, manager_id=manager.id)
    return _roster_text(tasks)


def _roster_text(tasks: Sequence[Task]) -> str:
    lines = ["[Task roster]"]
    used = 0
    for index, task in enumerate(tasks):
        entry = _roster_entry(task)
        if index and used + len(entry) > _ROSTER_MAX_CHARS:
            lines.append(
                f"… and {len(tasks) - index} more; list them with "
                "GET /v1/agent-tasks?manager_conversation_id=<your session id>"
            )
            break
        lines.append(entry)
        used += len(entry) + 1
    if not tasks:
        lines.append("(no tasks yet)")
    return "\n".join(lines)


def _roster_entry(task: Task) -> str:
    line = f"{task.id} — {task.title} ({task.state})"
    note = (task.internal_note or "").strip()
    if not note:
        return line
    return f"{line}\n  Note: " + "\n  ".join(note.splitlines())


def _owner_parts(role: BuiltinRole, session_id: str, conversation_store: Any) -> list[str]:
    settings = open_user_settings_store(conversation_store)
    board = open_board_stores(conversation_store)
    if settings is None or board is None:
        return [role.default_manual()]
    # Without an owner grant (single-user mode) the local user's settings and board apply.
    owner = conversation_store.get_session_owner(session_id, owner_only=True)
    if role is WATCHER_ROLE:
        return _watcher_parts(session_id, board, owner)
    overrides = settings.get_globalhub_role_overrides(role.key, user_id=owner)
    parts = [role_definition(role, overrides).manual]
    if role.key == MANAGER_ROLE_KEY:
        manager = board.managers.get_by_conversation_id(session_id, owner_user_id=owner)
        if manager is not None:
            parts.append(_MANAGER_IDS.format(session_id=session_id, manager_id=manager.id))
    return parts


def _watcher_parts(session_id: str, board: BoardStores, owner: str | None) -> list[str]:
    watcher = board.watchers.get_by_conversation_id(session_id, owner_user_id=owner)
    if watcher is None:
        # The session of a deleted watcher is an ordinary session.
        return [WATCHER_ROLE.default_manual()]
    return [
        watcher.manual or WATCHER_ROLE.default_manual(),
        _WATCHER_IDS.format(name=watcher.name, watcher_id=watcher.id),
    ]
