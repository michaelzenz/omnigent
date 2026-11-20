"""Tests for the ids of the changes that the GlobalHub custodian stages."""

from __future__ import annotations

from omnigent.agent_tasks.notice_changes import change_id


def test_a_change_id_names_the_kind_and_subject_and_fits_a_toggle_id() -> None:
    long_subject = {"kind": "delete_session", "session": "s" * 200}

    hashed = change_id(long_subject)

    assert change_id({"kind": "merge_tasks", "keep": "t1", "merge": ["t2"]}) == "merge_tasks__t1"
    assert change_id({"kind": "split_task", "task": "t3", "new_title": "x"}) == "split_task__t3"
    assert hashed.startswith("delete_session__") and len(hashed) <= 64
    assert hashed == change_id(dict(long_subject)) != change_id({**long_subject, "session": "t"})
