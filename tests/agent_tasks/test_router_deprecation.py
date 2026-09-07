"""Tests for the deprecated tag-router flag and the roster-based manager flow."""

from __future__ import annotations

import pytest

from omnigent.agent_tasks import ingress as ingress_mod
from omnigent.agent_tasks.constants import TAG_ROUTER_ENABLED
from omnigent.agent_tasks.notices import _format_manager_notice, _format_task_roster
from omnigent.agent_tasks.resolve import dismiss_task_event
from omnigent.agent_tasks.scoring import rank_tasks_for_event_tags
from omnigent.entities import EventTag, Task, TaskEvent, TaskTag
from omnigent.errors import OmnigentError
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _event(state: str = "routed") -> TaskEvent:
    return TaskEvent(
        id="ev1",
        event_type="session.turn.finished",
        title="t",
        state=state,
    )


def _task(task_id: str, title: str, state: str = "active") -> Task:
    return Task(
        id=task_id,
        title=title,
        goal="",
        state=state,
        manager_role_key="manager:default",
        owner_user_id=None,
        description=None,
        internal_note=None,
        created_at=0,
    )


# ── Dismiss idempotency ───────────────────────────────


class TestDismissIdempotent:
    @pytest.mark.parametrize("state", ["reconciled", "dismissed", "failed", "broadcast"])
    def test_settled_states_returned_unchanged(self, state: str):
        """Dismiss on an already-settled event is a no-op, not an error."""
        event = _event(state)
        updated = dismiss_task_event(event=event, task_event_store=None)
        assert updated.state == state

    def test_routed_event_is_dismissed(self, db_uri: str):
        store = SqlAlchemyTaskEventStore(db_uri)
        event = store.create_event("ev1", "session.turn.finished", "t", state="routed")
        updated = dismiss_task_event(event=event, task_event_store=store)
        assert updated.state == "dismissed"

    def test_stale_event_object_still_dismisses(self, db_uri: str):
        """A stale snapshot of a settled event no longer 409s — it no-ops."""
        store = SqlAlchemyTaskEventStore(db_uri)
        store.create_event("ev1", "session.turn.finished", "t", state="reconciled")
        stale = _event("routed")  # caller's snapshot says routed
        stale.id = "ev1"
        updated = dismiss_task_event(event=stale, task_event_store=store)
        # The store re-reads current state; reconciled is a no-op state.
        assert updated.state == "reconciled"


# ── Router flag gating ────────────────────────────────


class TestRouterFlag:
    def test_flag_defaults_off(self):
        assert TAG_ROUTER_ENABLED is False

    def test_ingress_skips_scorer_when_disabled(self, db_uri, monkeypatch):
        """Unbound tagged events stall to the broker instead of auto-routing."""
        assert TAG_ROUTER_ENABLED is False
        stalled: list[TaskEvent] = []

        def fake_stall(*, event, task_event_store, owner_user_id):
            stalled.append(event)
            return event

        monkeypatch.setattr(ingress_mod, "_stall", fake_stall)
        event = TaskEvent(
            id="ev1",
            event_type="poll.github.pr_merged",
            title="PR merged",
            state="received",
            tags=[EventTag(tag_type="repo", tag="universe")],
        )

        import asyncio

        from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

        task_store = SqlAlchemyTaskStore(db_uri)
        task_store.create("task1", title="T", goal="")
        asyncio.run(
            ingress_mod.ingress_event(
                event=event,
                task_store=task_store,
                task_event_store=SqlAlchemyTaskEventStore(db_uri),
                worker_store=None,
                conversation_store=None,
            )
        )
        assert stalled and stalled[0].id == "ev1"

    def test_ranking_helpers_still_work_when_restored(self, db_uri):
        """The scorer machinery is intact for the flag=True escape hatch."""
        from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

        store = SqlAlchemyTaskStore(db_uri)
        store.create(
            "task1",
            title="T",
            goal="",
            tags=[TaskTag(task_id="task1", tag_type="repo", tag="universe")],
        )
        ranked = rank_tasks_for_event_tags(
            event_tags=[EventTag(tag_type="repo", tag="universe")],
            tasks=[store.get("task1")],
            task_store=store,
        )
        assert ranked and ranked[0][0].id == "task1"


# ── Roster rendering ──────────────────────────────────


class TestTaskRoster:
    def test_empty_roster_is_none(self):
        assert _format_task_roster([]) is None

    def test_roster_lists_ranked_tasks(self):
        block = _format_task_roster(
            [("t1", "Fix auth", "active"), ("t2", "Migrate DB", "pending")]
        )
        assert "[Task roster" in block
        assert "t1 — Fix auth (active)" in block
        assert "t2 — Migrate DB (pending)" in block
        assert "POST /v1/agent-tasks/batch" in block

    def test_roster_respects_token_budget(self):
        roster = [(f"t{i}", "x" * 200, "active") for i in range(50)]
        block = _format_task_roster(roster, max_tokens=100)
        assert "and 49 more ranked lower" in block
        # Budget: ~400 chars of lines.
        assert len(block) < 1200


class TestNoticeEnvelope:
    def test_events_text_is_events_only(self):
        """_format_manager_notice renders events only — no roster block."""
        import time

        from omnigent.entities import TaskEvent

        ev = TaskEvent(
            id="e",
            event_type="poll.github.pr_merged",
            title="PR #12 merged",
            state="routed",
            created_at=int(time.time() * 1000),
        )
        events_text = _format_manager_notice([ev])
        assert "PR #12 merged" in events_text
        assert "Task roster" not in events_text

    def test_payload_envelope_roundtrip(self):
        from omnigent.agent_tasks.queue.packagers import (
            build_notice_payload,
            parse_notice_payload,
        )

        payload = build_notice_payload(
            events_text="[System: 1 event(s) routed]",
            roster_text="[Task roster]\nt1 — Fix auth (active)",
        )
        events, roster = parse_notice_payload(payload)
        assert events == "[System: 1 event(s) routed]"
        assert roster.startswith("[Task roster]")

    def test_legacy_payload_is_events_only(self):
        from omnigent.agent_tasks.queue.packagers import parse_notice_payload

        events, roster = parse_notice_payload("legacy plain notice")
        assert events == "legacy plain notice"
        assert roster is None


class TestManagerCreationFallback:
    def test_response_built_from_row_when_discovery_omits(self):
        """create_manager must not fail when discovery filters the fresh manager.

        The discovery listing filters managers with an incomplete snapshot;
        a freshly-spawned manager with harness=NULL (engine from the bound
        bundle) must still be returned — the durable row is authoritative.
        """
        from omnigent.entities import Manager
        from omnigent.server.routes.agent_tasks import (
            _manager_to_response_from_row,
        )

        row = Manager(
            id="m1",
            owner_user_id="u",
            role_key="manager:default",
            title=None,
            description="Handles probes",
            conversation_id="conv1",
            host_id="h",
            workspace="~/",
            harness=None,  # NULL — engine from the bundle
            model="databricks-glm-5-2",
            agent_profile_id="ag",
            prompt_profile_id=None,
            created_at=0,
            updated_at=0,
        )
        resp = _manager_to_response_from_row(row, task_count=0)
        assert resp["id"] == "m1"
        assert resp["title"] == "Handles probes"  # falls back to description
        assert resp["task_count"] == 0


class TestStaleExecutionTargetRebind:
    def test_stale_target_detected_as_mismatch(self, db_uri):
        """A role profile bound to the wrong execution target self-heals.

        Rows seeded before execution_target_for_role existed (or before a
        re-mapping) carry a stale agent_profile_id; ensure_role_profile
        must treat the mismatch as stale and rebind.
        """
        import uuid

        from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_ROLE
        from omnigent.agent_tasks.broker_session import ensure_role_profile
        from omnigent.execution_targets import ONIH_PUPPYGARDEN_TARGET
        from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
        from omnigent.stores.task_role_profile_store.sqlalchemy_store import SqlAlchemyTaskRoleProfileStore

        agent_store = SqlAlchemyAgentStore(db_uri)
        wrong = generate_agent_id()
        right = generate_agent_id()
        agent_store.create(wrong, name="onih-openai-agents", bundle_location="test:///w")
        agent_store.create(right, name=ONIH_PUPPYGARDEN_TARGET, bundle_location="test:///r")
        profile_store = SqlAlchemyTaskRoleProfileStore(db_uri)
        profile_store.upsert(
            TASK_MANAGER_ROLE,
            agent_profile_id=wrong,  # stale: full agent, not the restricted target
            prompt_profile_id=None,
            harness=None,
            host_id=None,
            workspace="~/",
        )

        profile = ensure_role_profile(
            role=TASK_MANAGER_ROLE,
            auth_user_id=None,
            task_role_profile_store=profile_store,
            agent_store=agent_store,
        )
        assert profile.agent_profile_id == right
