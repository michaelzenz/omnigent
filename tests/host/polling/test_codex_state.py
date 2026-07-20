"""Tests for Codex bridge state helpers."""

from __future__ import annotations

from omnigent.host.polling.pollers.codex_state import (
    BridgeState,
    BridgeStateDelta,
    TrackedRollout,
    apply_bridge_delta,
    merge_bridge_deltas,
)


def test_merge_bridge_deltas_combines_updates_and_removals() -> None:
    tracked = TrackedRollout(
        thread_id="thread-a",
        rollout_path="/tmp/a.jsonl",
        session_id="session-a",
        byte_offset=1,
    )
    state = BridgeState(
        threads={
            "thread-a": tracked,
            "thread-b": TrackedRollout(
                thread_id="thread-b",
                rollout_path="/tmp/b.jsonl",
                session_id="session-b",
                byte_offset=2,
            ),
        }
    )
    updated = TrackedRollout(
        thread_id="thread-a",
        rollout_path="/tmp/a.jsonl",
        session_id="session-a",
        byte_offset=9,
    )
    merged = merge_bridge_deltas(
        BridgeStateDelta(updated={"thread-a": updated}, removed=set()),
        BridgeStateDelta(updated={}, removed={"thread-b"}),
    )
    result = apply_bridge_delta(state, merged)
    assert "thread-b" not in result.threads
    assert result.threads["thread-a"].byte_offset == 9
