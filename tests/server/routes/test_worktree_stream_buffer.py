"""Regression tests for worktree creation progress buffering."""

from omnigent.server.routes._sessions.common import _session_worktree_status_cache
from omnigent.server.routes._sessions.helpers import (
    _publish_worktree_log,
    _publish_worktree_status,
)


def test_worktree_logs_survive_late_stream_connection_and_failure() -> None:
    """Early git lines remain available in the session snapshot."""
    session_id = "worktree-log-buffer-test"
    try:
        _publish_worktree_status(session_id, "creating", branch="feature/logs")
        _publish_worktree_log(session_id, "Resolving repository root…")
        _publish_worktree_log(session_id, "Preparing worktree")

        status = _session_worktree_status_cache[session_id]
        assert status.log_lines == [
            "Resolving repository root…",
            "Preparing worktree",
        ]

        _publish_worktree_status(
            session_id,
            "failed",
            branch="feature/logs",
            error="checkout failed",
        )
        failed = _session_worktree_status_cache[session_id]
        assert failed.log_lines == status.log_lines
    finally:
        _session_worktree_status_cache.pop(session_id, None)


def test_launching_stage_is_retained_and_ready_evicts() -> None:
    """The worktree-done/runner-starting stage keeps logs and cache until ready."""
    session_id = "worktree-launching-stage-test"
    try:
        _publish_worktree_status(session_id, "creating", branch="feature/x")
        _publish_worktree_log(session_id, "Preparing worktree")
        _publish_worktree_status(session_id, "launching", branch="feature/x")
        status = _session_worktree_status_cache[session_id]
        assert status.stage == "launching"
        assert status.log_lines == ["Preparing worktree"]
        _publish_worktree_status(session_id, "ready", branch="feature/x")
        assert session_id not in _session_worktree_status_cache
    finally:
        _session_worktree_status_cache.pop(session_id, None)
