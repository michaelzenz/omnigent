"""Agent-task event store — events, routing, and executions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import (
    EventTag,
    TaskEvent,
    TaskEventExecution,
    TaskEventRoutingAttempt,
    TaskEventSubscription,
)

_UNSET: Any = object()


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
        manager_conversation_id: str | None = None,
        payload: str | None = None,
        source: str | None = None,
        source_key: str | None = None,
        source_offset: str | None = None,
        source_internal_session_id: str | None = None,
        parent_event_id: str | None = None,
        state: str = "received",
        tags: list[EventTag] | None = None,
        owner_user_id: str | None = None,
    ) -> TaskEvent:
        """Insert a new task event."""

    @abstractmethod
    def get_event(self, event_id: str) -> TaskEvent | None:
        """Return an event by id, or ``None`` if not found."""

    @abstractmethod
    def get_events(self, event_ids: list[str]) -> list[TaskEvent]:
        """Return events for the given ids (order unspecified; missing ids skipped)."""

    @abstractmethod
    def get_event_by_source(
        self,
        *,
        source: str,
        source_key: str,
        source_offset: str,
        event_type: str,
    ) -> TaskEvent | None:
        """Return an ingress-deduped canonical event, if one already exists.

        Only canonical rows (``parent_event_id`` NULL) participate in dedup;
        subscription fan-out copies share the source tuple but are ignored.
        """

    @abstractmethod
    def list_events(
        self,
        *,
        state: str | None = None,
        task_id: str | None = None,
        event_type: str | None = None,
    ) -> list[TaskEvent]:
        """List events ordered by ``created_at DESC, id DESC``."""

    @abstractmethod
    def update_event(
        self,
        event_id: str,
        *,
        task_id: str | None = _UNSET,
        manager_conversation_id: str | None = _UNSET,
        state: str | None = None,
        routed_at: int | None = None,
        processed_at: int | None = None,
        owner_user_id: str | None = _UNSET,
    ) -> TaskEvent | None:
        """Update mutable event fields."""

    @abstractmethod
    def reconcile_events_to_task(
        self,
        event_ids: list[str],
        *,
        task_id: str,
        manager_conversation_id: str | None,
    ) -> list[TaskEvent]:
        """Atomically reconcile routed events assigned to one task."""

    @abstractmethod
    def route_events_to_manager(
        self,
        event_ids: list[str],
        *,
        manager_conversation_id: str,
        owner_user_id: str,
        routable_states: frozenset[str],
    ) -> list[TaskEvent] | None:
        """Atomically route a batch, returning ``None`` on any conflict."""

    @abstractmethod
    def get_event_tags(self, event_id: str) -> list[EventTag]:
        """Return immutable ingress tags for an event."""

    # ── Subscriptions ────────────────────────────────────────────

    @abstractmethod
    def create_subscription(
        self,
        subscription_id: str,
        task_id: str,
        *,
        source: str,
        source_key: str,
        owner_user_id: str | None = None,
    ) -> TaskEventSubscription:
        """Subscribe a task to an event ``(source, source_key)`` pair.

        Idempotent: an existing subscription for the same tuple is returned.
        """

    @abstractmethod
    def get_subscription(self, subscription_id: str) -> TaskEventSubscription | None:
        """Return a subscription by id, or ``None`` if not found."""

    @abstractmethod
    def list_subscriptions(
        self,
        *,
        source: str,
        source_key: str,
    ) -> list[TaskEventSubscription]:
        """List subscriptions matching an event ``(source, source_key)`` pair."""

    @abstractmethod
    def list_subscriptions_for_task(self, task_id: str) -> list[TaskEventSubscription]:
        """List a task's subscriptions ordered by ``created_at ASC, id ASC``."""

    @abstractmethod
    def delete_subscription(self, subscription_id: str) -> bool:
        """Delete a subscription. Returns whether a row was removed."""

    @abstractmethod
    def delete_subscriptions_for_task(self, task_id: str) -> int:
        """Delete all subscriptions for a task. Returns count deleted."""

    @abstractmethod
    def list_deliveries_for_event(self, parent_event_id: str) -> list[TaskEvent]:
        """List subscription fan-out copies of a canonical event, ``created_at ASC``."""

    # ── Routing ──────────────────────────────────────────────────

    @abstractmethod
    def create_routing_attempt(
        self,
        attempt_id: str,
        event_id: str,
        candidate_task_id: str,
        *,
        score: float | None = None,
        reason: str | None = None,
        proposed_at: int | None = None,
    ) -> TaskEventRoutingAttempt:
        """Insert one routing attempt row."""

    @abstractmethod
    def list_routing_attempts(self, event_id: str) -> list[TaskEventRoutingAttempt]:
        """List routing attempts for an event ordered by ``proposed_at ASC, id ASC``."""

    # ── Executions ───────────────────────────────────────────────

    @abstractmethod
    def create_execution(
        self,
        execution_id: str,
        task_item_id: str,
        task_id: str,
        *,
        status: str = "queued",
        attempt_no: int = 1,
        agent_queue_item_id: str | None = None,
        conversation_id: str | None = None,
        assigned_at: int | None = None,
    ) -> TaskEventExecution:
        """Insert one worker execution row."""

    @abstractmethod
    def get_execution(self, execution_id: str) -> TaskEventExecution | None:
        """Return an execution by id."""

    @abstractmethod
    def get_execution_by_agent_queue_item_id(
        self,
        agent_queue_item_id: str,
    ) -> TaskEventExecution | None:
        """Return the execution created by one queue delivery."""

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
    def list_executions_for_task(self, task_id: str) -> list[TaskEventExecution]:
        """List executions for a task ordered by ``created_at DESC, id DESC``."""

    @abstractmethod
    def list_executions_for_item(self, task_item_id: str) -> list[TaskEventExecution]:
        """List executions for a task item ordered by ``attempt_no ASC, id ASC``."""

    @abstractmethod
    def list_executions_by_status(self, status: str) -> list[TaskEventExecution]:
        """List executions in one lifecycle state."""

    # ── GC ─────────────────────────────────────────────────────

    @abstractmethod
    def purge_old_events(
        self,
        *,
        before_ts: int,
        states: list[str],
        event_type: str | None = None,
    ) -> int:
        """Delete events in the given states older than ``before_ts``.

        When ``event_type`` is set, only events of that type are purged.

        :returns: Number of rows deleted.
        """
