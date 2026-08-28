"""Tests for managed task event type helpers."""

from __future__ import annotations

from omnigent.agent_tasks.event_types import (
    is_ingress_candidate,
    is_session_internal_event,
)


def test_ingress_candidate_filter() -> None:
    assert is_ingress_candidate("build.finished") is True
    assert is_ingress_candidate("session.adoption") is False
    assert is_ingress_candidate("session.adopted") is False


def test_session_internal_event_detection() -> None:
    assert is_session_internal_event("session.adoption") is True
    assert is_session_internal_event("build.finished") is False
