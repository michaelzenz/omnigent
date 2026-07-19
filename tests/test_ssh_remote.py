"""Tests for remote SSH file helpers."""

from omnigent.ssh_remote import (
    _parse_rollout_listing,
    _remote_codex_home,
)


def test_remote_codex_home_expands_tilde() -> None:
    assert _remote_codex_home("~/.codex") == "$HOME/.codex"
    assert _remote_codex_home("~/foo") == "$HOME/foo"
    assert _remote_codex_home("/abs/path") == "/abs/path"


def test_parse_rollout_listing_sorts_newest_first() -> None:
    stdout = (
        b"/old/rollout.jsonl\0"
        b"100\0"
        b"/new/rollout.jsonl\0"
        b"200\0"
    )
    rollouts = _parse_rollout_listing(stdout)
    assert [entry.path for entry in rollouts] == ["/new/rollout.jsonl", "/old/rollout.jsonl"]
    assert rollouts[0].mtime_ms == 200_000
