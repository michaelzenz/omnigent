"""Bootstrap helpers for the per-user lightweight task secretary conversation.

The secretary is an on-demand Q&A agent: it remembers the task-system endpoints
and answers the user's questions. It has no packager, no queue, and no dispatch
handler — the user chats with it directly. Only session bootstrap lives here.
"""

from __future__ import annotations

from omnigent.agent_tasks.constants import resolve_task_harness
from omnigent.agent_tasks.session_labels import ROLE_LABEL, SECRETARY_ROLE_VALUE
from omnigent.native_coding_agents import native_coding_agent_for_harness
from omnigent.stores.conversation_store import ConversationStore


def apply_secretary_session_labels(
    conversation_store: ConversationStore,
    conversation_id: str,
    *,
    harness: str,
) -> None:
    labels = {ROLE_LABEL: SECRETARY_ROLE_VALUE}
    native_agent = native_coding_agent_for_harness(resolve_task_harness(harness))
    if native_agent is not None:
        labels.update(native_agent.presentation_labels)
    conversation_store.set_labels(conversation_id, labels)
