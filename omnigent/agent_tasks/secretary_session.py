"""Bootstrap helpers for the per-user lightweight task secretary conversation.

The secretary is an on-demand Q&A agent: it remembers the task-system endpoints
and answers the user's questions. It has no packager, no queue, and no dispatch
handler — the user chats with it directly. Only session bootstrap lives here.
"""

from __future__ import annotations

from omnigent.agent_tasks.constants import resolve_task_harness
from omnigent.agent_tasks.session_labels import ROLE_LABEL, SECRETARY_ROLE_VALUE
from omnigent.db.utils import generate_task_id
from omnigent.entities import MessageData, NewConversationItem
from omnigent.native_coding_agents import native_coding_agent_for_harness
from omnigent.stores.conversation_store import ConversationStore

SECRETARY_MANUAL_PATH = "docs/agent-tasks/TASK_SECRETARY.md"
API_REFERENCE_PATH = "docs/agent-tasks/API_REFERENCE.md"

SECRETARY_SEED_PROMPT = (
    "You are the task secretary of the PuppyGarden task system — a lightweight "
    "assistant that remembers the available endpoints and answers user questions "
    "about the task system. Read and follow " + SECRETARY_MANUAL_PATH + ". When you "
    "need to recall an endpoint's shape or parameters, read " + API_REFERENCE_PATH + "."
)


def seed_secretary_prompt(conversation_store: ConversationStore, conversation_id: str) -> None:
    """Append a short manual pointer as hidden agent context (not shown in the UI)."""
    item = NewConversationItem(
        type="message",
        response_id=generate_task_id(),
        data=MessageData(
            role="user",
            content=[{"type": "input_text", "text": SECRETARY_SEED_PROMPT}],
            is_meta=True,
        ),
    )
    conversation_store.append(conversation_id, [item])


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
