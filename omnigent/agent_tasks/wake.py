"""Best-effort wake delivery for managed task managers and secretaries."""

from __future__ import annotations

import logging

from omnigent.entities import Task, TaskEvent, TaskEventExecution
from omnigent.runner.routing import RunnerRouter
from omnigent.server.routes.sessions import _wake_parent_for_blocked_child
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.secretary_profile_store import SecretaryProfileStore

_logger = logging.getLogger(__name__)


def _format_event_notice(event: TaskEvent) -> str:
    summary = event.summary or ""
    summary_block = f"\n{summary}" if summary else ""
    return (
        f"[System: task event {event.id} routed to this manager] "
        f"{event.title}{summary_block}"
    )


async def wake_task_manager_for_execution(
    *,
    manager_conversation_id: str,
    execution: TaskEventExecution,
    event: TaskEvent | None,
    conversation_store: ConversationStore,
    runner_router: RunnerRouter | None,
) -> bool:
    """Wake the task manager when a worker execution reaches a terminal state."""
    conv = conversation_store.get_conversation(manager_conversation_id)
    if conv is None:
        _logger.warning(
            "task manager wake skipped: conversation %s missing for execution %s",
            manager_conversation_id,
            execution.id,
        )
        return False
    event_title = event.title if event is not None else execution.event_id
    summary = execution.result_summary or execution.error or ""
    summary_block = f"\n{summary}" if summary else ""
    notice = (
        f"[System: worker execution {execution.id} {execution.status} "
        f"for event {event_title}]{summary_block}"
    )
    return await _wake_parent_for_blocked_child(
        manager_conversation_id,
        conv,
        notice,
        conversation_store=conversation_store,
        runner_router=runner_router,
    )


async def wake_task_manager_for_event(
    *,
    manager_conversation_id: str,
    event: TaskEvent,
    conversation_store: ConversationStore,
    runner_router: RunnerRouter | None,
) -> bool:
    """
    Inject a synthetic user message into the manager session.

    Best-effort: transport failures are logged and reported as ``False``.
    """
    conv = conversation_store.get_conversation(manager_conversation_id)
    if conv is None:
        _logger.warning(
            "task manager wake skipped: conversation %s missing for event %s",
            manager_conversation_id,
            event.id,
        )
        return False
    return await _wake_parent_for_blocked_child(
        manager_conversation_id,
        conv,
        _format_event_notice(event),
        conversation_store=conversation_store,
        runner_router=runner_router,
    )


def _format_secretary_stall_notice(
    events: list[TaskEvent],
    ranked_candidates: dict[str, list[tuple[Task, float]]],
) -> str:
    lines = [f"[System: {len(events)} task event(s) need routing decisions]"]
    for event in events:
        candidates = ranked_candidates.get(event.id, [])
        candidate_text = ""
        if candidates:
            rendered = ", ".join(
                f"{task.title} ({score:.2f})" for task, score in candidates[:3]
            )
            candidate_text = f" — candidates: {rendered}"
        lines.append(f"- {event.event_type}: {event.title!r} ({event.state}){candidate_text}")
    return "\n".join(lines)


async def wake_secretary_for_stalled_events(
    *,
    user_id: str,
    events: list[TaskEvent],
    ranked_candidates: dict[str, list[tuple[Task, float]]],
    secretary_profile_store: SecretaryProfileStore,
    conversation_store: ConversationStore,
    runner_router: RunnerRouter | None,
) -> bool:
    """Wake the task secretary when routing stalls and needs user input."""
    if not events:
        return False
    profile = secretary_profile_store.get(user_id)
    if profile is None or profile.conversation_id is None:
        _logger.warning(
            "secretary wake skipped: no live session for user %s (%s event(s))",
            user_id,
            len(events),
        )
        return False
    conv = conversation_store.get_conversation(profile.conversation_id)
    if conv is None:
        _logger.warning(
            "secretary wake skipped: conversation %s missing",
            profile.conversation_id,
        )
        return False
    notice = _format_secretary_stall_notice(events, ranked_candidates)
    return await _wake_parent_for_blocked_child(
        profile.conversation_id,
        conv,
        notice,
        conversation_store=conversation_store,
        runner_router=runner_router,
    )
