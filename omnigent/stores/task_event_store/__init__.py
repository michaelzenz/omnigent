"""Agent-task event store — events, routing, executions, and session bindings."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import (
    TaskEvent,
    TaskEventExecution,
    TaskEventRoutingAttempt,
    TaskEventRoutingResolution,
    TaskEventTag,
    TaskSessionBinding,
)

_UNSET: Any = object()

TASK_SESSION_BINDING_KINDS = frozenset({"ambient", "worker", "manager"})


class TaskEventStore(ABC):
    """Abstract base for task-event persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    # ── Events ───────────────────────────────────────────────────

    @abstractmethod
    def create_event(
        self,
        event_id: str,
        event_type: str,
        title: str,
        *,
        task_id: str | None = None,
        payload: str | None = None,
        source: str | None = None,
        source_key: str | None = None,
        source_offset: int | None = None,
        source_internal_session_id: str | None = None,
        state: str = "received",
        tags: list[TaskEventTag] | None = None,
    ) -> TaskEvent:
        """Insert a new task event."""

    @abstractmethod
    def get_event(self, event_id: str) -> TaskEvent | None:
        """Return an event by id, or ``None`` if not found."""

    @abstractmethod
    def get_event_by_source(
        self,
        *,
        source: str,
        source_key: str,
        source_offset: int,
        event_type: str,
    ) -> TaskEvent | None:
        """Return an ingress-deduped event, if one already exists."""

    @abstractmethod
    def list_events(
        self,
        *,
        state: str | None = None,
        task_id: str | None = None,
    ) -> list[TaskEvent]:
        """List events ordered by ``created_at DESC, id DESC``."""

    @abstractmethod
    def update_event(
        self,
        event_id: str,
        *,
        task_id: str | None = _UNSET,
        state: str | None = None,
        selected_routing_attempt_id: str | None = _UNSET,
        routed_at: int | None = None,
        processed_at: int | None = None,
    ) -> TaskEvent | None:
        """Update mutable event fields."""

    @abstractmethod
    def get_event_tags(self, event_id: str) -> list[TaskEventTag]:
        """Return immutable ingress tags for an event."""

    # ── Routing ──────────────────────────────────────────────────

    @abstractmethod
    def create_routing_attempt(
        self,
        attempt_id: str,
        event_id: str,
        candidate_task_id: str,
        candidate_manager_agent_id: str,
        rank: int,
        *,
        score: float | None = None,
        decision: str = "proposed",
        manager_reason: str | None = None,
        proposed_at: int | None = None,
        responded_at: int | None = None,
        selected_at: int | None = None,
    ) -> TaskEventRoutingAttempt:
        """Insert one routing attempt row."""

    @abstractmethod
    def update_routing_attempt(
        self,
        attempt_id: str,
        *,
        decision: str | None = None,
        manager_reason: str | None = None,
        responded_at: int | None = None,
        selected_at: int | None = None,
    ) -> TaskEventRoutingAttempt | None:
        """Update one routing attempt row."""

    @abstractmethod
    def list_routing_attempts(self, event_id: str) -> list[TaskEventRoutingAttempt]:
        """List routing attempts for an event ordered by ``rank ASC, id ASC``."""

    @abstractmethod
    def create_resolution(
        self,
        resolution_id: str,
        event_id: str,
        selected_attempt_id: str,
        selected_task_id: str,
        selected_manager_agent_id: str,
        *,
        resolved_by_user_id: str | None = None,
        resolution_note: str | None = None,
        created_at: int | None = None,
    ) -> TaskEventRoutingResolution:
        """Record the user's final routing choice."""

    @abstractmethod
    def get_resolution(self, event_id: str) -> TaskEventRoutingResolution | None:
        """Return the latest resolution for an event, if any."""

    # ── Executions ───────────────────────────────────────────────

    @abstractmethod
    def create_execution(
        self,
        execution_id: str,
        task_item_id: str,
        task_id: str,
        manager_agent_id: str,
        worker_agent_id: str,
        *,
        event_id: str | None = None,
        status: str = "queued",
        attempt_no: int = 1,
        conversation_id: str | None = None,
        assigned_at: int | None = None,
    ) -> TaskEventExecution:
        """Insert one worker execution row."""

    @abstractmethod
    def get_execution(self, execution_id: str) -> TaskEventExecution | None:
        """Return an execution by id."""

    @abstractmethod
    def get_execution_by_conversation_id(
        self,
        conversation_id: str,
    ) -> TaskEventExecution | None:
        """Return the newest execution for a worker session."""

    @abstractmethod
    def update_execution(
        self,
        execution_id: str,
        *,
        status: str | None = None,
        conversation_id: str | None = _UNSET,
        started_at: int | None = None,
        finished_at: int | None = None,
        result_summary: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
    ) -> TaskEventExecution | None:
        """Update one execution row."""

    @abstractmethod
    def list_executions_for_event(self, event_id: str) -> list[TaskEventExecution]:
        """List executions for an event ordered by ``attempt_no ASC, id ASC``."""

    @abstractmethod
    def list_executions_for_task(self, task_id: str) -> list[TaskEventExecution]:
        """List executions for a task ordered by ``created_at DESC, id DESC``."""

    @abstractmethod
    def list_executions_for_item(self, task_item_id: str) -> list[TaskEventExecution]:
        """List executions for a task item ordered by ``attempt_no ASC, id ASC``."""

    # ── Session bindings ───────────────────────────────────────────

    @abstractmethod
    def get_binding(self, session_id: str) -> TaskSessionBinding | None:
        """Return the task binding for a session, if any."""

    @abstractmethod
    def upsert_binding(
        self,
        session_id: str,
        task_id: str,
        manager_agent_id: str,
        binding_kind: str,
        *,
        manager_conversation_id: str | None = None,
        created_at: int | None = None,
    ) -> TaskSessionBinding:
        """Create or replace a session-to-task binding."""

    @abstractmethod
    def delete_binding(self, session_id: str) -> bool:
        """Delete a session binding. Idempotent."""
