"""The title and live state of GlobalHub sessions, as main's session list shows them."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from omnigent.entities import Conversation
from omnigent.runtime import pending_elicitations
from omnigent.server.agent_task_delivery import SessionSendDeps, settle_orphaned_turns
from omnigent.server.routes import sessions as session_routes
from omnigent.server.schemas import AgentTaskBoardSession
from omnigent.stores import ConversationStore


async def read_session_states(
    conversations: Sequence[Conversation],
    *,
    user_id: str | None,
    conversation_store: ConversationStore,
    deps: SessionSendDeps,
) -> dict[str, AgentTaskBoardSession]:
    """
    Read each session's title and live state, as the sidebar shows them.

    A turn whose runner is gone settles first. Conversations that are not
    sessions are left out.

    :param conversations: The sessions to read.
    :param user_id: The viewer, or ``None`` in single-user mode.
    :param conversation_store: Holds the sessions' sub-agents.
    :param deps: Reads runner liveness for the settle.
    :returns: The states by session id.
    """
    found = [conv for conv in conversations if conv.agent_id is not None]
    if not found:
        return {}
    # A settled session publishes its new status, which the rows below read.
    await settle_orphaned_turns(found, deps=deps)
    ids = [conv.id for conv in found]
    children = await asyncio.to_thread(
        conversation_store.list_child_conversation_ids_by_parent, ids
    )
    pending = pending_elicitations.counts_for(ids)
    return {
        conv.id: _session_state(conv, user_id, pending[conv.id], children.get(conv.id, []))
        for conv in found
    }


def _session_state(
    conv: Conversation, user_id: str | None, pending_count: int, child_session_ids: list[str]
) -> AgentTaskBoardSession:
    # Main's session-list row, so a session reads as it does in the sidebar.
    row = session_routes._build_session_list_item(
        conv,
        agent_names_by_id={},
        grants=[],
        user_id=user_id,
        user_is_admin=False,
        permissions_enabled=False,
        pending_count=pending_count,
        child_session_ids=child_session_ids,
        comments_fingerprint=None,
    )
    return AgentTaskBoardSession(
        title=row.title,
        status=row.status,
        pending_elicitations_count=row.pending_elicitations_count,
        background_work_running=row.background_work_running,
        blocked_on=row.blocked_on,
        updated_at=row.updated_at,
    )
