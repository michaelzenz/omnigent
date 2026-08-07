"""Tests for remote SSH file helpers."""

from omnigent.ssh_remote import bash_lc


def test_bash_lc_quotes_the_whole_command() -> None:
    """The remote shell must see one argv word, whatever the command contains."""
    assert bash_lc("echo hi") == "bash -lc 'echo hi'"


def test_bash_lc_escapes_embedded_single_quotes() -> None:
    wrapped = bash_lc("echo 'a b'")
    assert wrapped.startswith("bash -lc ")
    assert "'\"'\"'" in wrapped
