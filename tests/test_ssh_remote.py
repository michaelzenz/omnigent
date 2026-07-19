"""Tests for remote SSH file helpers."""

from omnigent.ssh_remote import _remote_codex_home


def test_remote_codex_home_expands_tilde() -> None:
    assert _remote_codex_home("~/.codex") == "$HOME/.codex"
    assert _remote_codex_home("~/foo") == "$HOME/foo"
    assert _remote_codex_home("/abs/path") == "/abs/path"
