"""Tests for managed task event type helpers."""

from __future__ import annotations

from omnigent.agent_tasks.event_types import (
    MANAGER_PROPOSAL,
    is_distributor_candidate,
    is_manager_internal_event,
    is_session_internal_event,
)


def test_manager_internal_event_detection() -> None:
    assert is_manager_internal_event(MANAGER_PROPOSAL) is True
    assert is_manager_internal_event("build.finished") is False


def test_distributor_candidate_filter() -> None:
    assert is_distributor_candidate(event_type="build.finished", task_id=None) is True
    assert is_distributor_candidate(event_type=MANAGER_PROPOSAL, task_id=None) is False
    assert is_distributor_candidate(event_type="session.adopted", task_id=None) is False
    assert is_distributor_candidate(event_type="build.finished", task_id="abc") is False


def test_session_internal_event_detection() -> None:
    assert is_session_internal_event("session.adoption") is True
    assert is_session_internal_event("build.finished") is False
