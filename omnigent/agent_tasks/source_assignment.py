"""Ask a user's custodian to assign the source types that sessions follow to watchers.

The server asks when a session first follows a source of a new type, and when a
watcher is created or deleted. One request waits in the custodian's queue at a
time; its message lists the types without a watcher when it is sent. The
built-in fallback watcher checks those types until the custodian assigns them.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from omnigent.agent_tasks.board_stores import BoardStores
from omnigent.agent_tasks.default_watchers import ensure_fallback_watcher
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_role_session
from omnigent.agent_tasks.roles import CUSTODIAN_ROLE_KEY, builtin_role
from omnigent.db.utils import now_epoch
from omnigent.errors import OmnigentError

_logger = logging.getLogger(__name__)

#: The queue item kind of a request that the custodian assign source types.
SOURCE_TYPES_KIND = "source_types"


async def request_source_assignment(
    board: BoardStores, sessions: RoleSessionStores | None, *, user_id: str | None
) -> bool:
    """
    Queue a request that the custodian assign source types, unless one already waits.

    The fallback watcher is created first when the user has none. Without a
    GlobalHub placement there is no custodian, and the request is skipped.

    :param board: Holds the queue, the watchers and their schedules.
    :param sessions: Where the custodian session lives; ``None`` on servers
        without GlobalHub roles.
    :param user_id: The owner, or ``None`` in single-user mode.
    :returns: Whether a request was queued.
    """
    await asyncio.to_thread(
        ensure_fallback_watcher,
        board.watchers,
        board.role_schedules,
        owner_user_id=user_id,
        now=now_epoch(),
    )
    if sessions is None:
        return False
    settings = sessions.user_settings_store
    if await asyncio.to_thread(settings.get_globalhub_placement, user_id=user_id) is None:
        return False
    custodian_role = builtin_role(CUSTODIAN_ROLE_KEY)
    assert custodian_role is not None
    try:
        custodian, _ = await ensure_role_session(custodian_role, user_id=user_id, stores=sessions)
    except OmnigentError:
        _logger.warning("GlobalHub could not reach the custodian of %s", user_id, exc_info=True)
        return False

    def enqueue() -> bool:
        waiting = board.queue.list(
            owner_user_id=user_id, states=("queued",), target_session_id=custodian.id
        )
        if any(item.kind == SOURCE_TYPES_KIND for item in waiting):
            return False
        board.queue.enqueue(
            uuid.uuid4().hex,
            owner_user_id=user_id,
            target_session_id=custodian.id,
            kind=SOURCE_TYPES_KIND,
        )
        return True

    return await asyncio.to_thread(enqueue)
