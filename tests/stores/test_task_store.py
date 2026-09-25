"""Tests for :class:`SqlAlchemyTaskStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.entities import TaskTag
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskStore:
    return SqlAlchemyTaskStore(db_uri)


def test_create_and_get_round_trip(store: SqlAlchemyTaskStore) -> None:
    task = store.create(
        task_id=_uid("task_1"),
        title="S3 reliability",
        goal="S3 uploads are reliable",
        owner_user_id="alice@example.com",
        internal_note="upload retries and backoff",
        manager_id=_uid("conv_mgr"),
        tags=[TaskTag(task_id=_uid("task_1"), tag_type="domain", tag="s3")],
    )
    assert task.id == _uid("task_1")
    assert task.manager_id == _uid("conv_mgr")
    # A task names the manager role that runs it; workers come from providers.
    assert task.manager_role_key == "manager:default"
    loaded = store.get(_uid("task_1"))
    assert loaded == task


def test_create_accepts_custom_manager_role_key(store: SqlAlchemyTaskStore) -> None:
    """The manager lane can be pointed at a custom glossary role."""
    task_id = _uid("task_roles")
    store.create(
        task_id=task_id,
        title="Research spike",
        goal="Research spike complete",
        manager_role_key="manager:research",
    )
    loaded = store.get(task_id)
    assert loaded is not None
    assert loaded.manager_role_key == "manager:research"
    assert store.count_by_manager_role_key("manager:research") == 1


def test_set_tags_replaces_task_tags(store: SqlAlchemyTaskStore) -> None:
    task_id = _uid("task_tags")
    store.create(
        task_id=task_id,
        title="Title",
        goal="goal",
        internal_note="routing context",
    )
    store.set_tags(
        task_id,
        [
            TaskTag(task_id=task_id, tag_type="domain", tag="ci"),
            TaskTag(task_id=task_id, tag_type="component", tag="build"),
        ],
    )
    tags = store.get_tags(task_id)
    assert {(tag.tag_type, tag.tag) for tag in tags} == {
        ("domain", "ci"),
        ("component", "build"),
    }


def test_list_task_ids_by_tag(store: SqlAlchemyTaskStore) -> None:
    task_a = _uid("task_a")
    task_b = _uid("task_b")
    store.create(task_id=task_a, title="A", goal="a goal")
    store.create(task_id=task_b, title="B", goal="b goal")
    store.set_tags(task_a, [TaskTag(task_id=task_a, tag_type="domain", tag="s3")])
    store.set_tags(task_b, [TaskTag(task_id=task_b, tag_type="domain", tag="s3")])
    assert sorted(store.list_task_ids_by_tag("domain", "s3")) == sorted([task_a, task_b])


def test_delete_removes_tags_and_workers(store: SqlAlchemyTaskStore) -> None:
    from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

    task_id = _uid("task_delete")
    session_id = _uid("sess_delete")
    store.create(task_id=task_id, title="Delete me", goal="deleted")
    store.set_tags(task_id, [TaskTag(task_id=task_id, tag_type="domain", tag="x")])
    worker_store = SqlAlchemyWorkerStore(store.storage_location)
    worker_store.create_worker(
        _uid("worker_delete"),
        task_id,
        target_id=session_id,
    )
    assert store.delete(task_id) is True
    assert store.get(task_id) is None
    assert store.get_tags(task_id) == []
    # Workers are durable: deleting the task marks them deleted, not removed.
    # Live lookups exclude the dead lane; the raw list still shows it.
    assert worker_store.get_by_target_id(session_id) is None
    durable = worker_store.list_workers_by_target_id(session_id)
    assert [worker.state for worker in durable] == ["deleted"]


def test_list_recent_orders_by_last_touch(store: SqlAlchemyTaskStore) -> None:
    old = _uid("task_old")
    bumped = _uid("task_bumped")
    store.create(task_id=old, title="Old", goal="old goal")
    store.create(task_id=bumped, title="Bumped", goal="bumped goal")
    store.update(bumped, title="Bumped!")
    recent = store.list_recent(5)
    assert recent[0].id == bumped
    assert {task.id for task in recent} == {old, bumped}


def test_list_recent_has_no_state_filter(store: SqlAlchemyTaskStore) -> None:
    archived = _uid("task_archived")
    store.create(task_id=archived, title="Archived", goal="gone")
    store.update(archived, state="archived")
    recent = store.list_recent(5)
    assert any(task.id == archived for task in recent)


def test_list_recent_respects_limit(store: SqlAlchemyTaskStore) -> None:
    for i in range(5):
        store.create(task_id=_uid(f"task_lim_{i}"), title=f"T{i}", goal="g")
    assert len(store.list_recent(3)) == 3


# ---------------------------------------------------------------------------
# move_to_queue_end — state-aware placement
# ---------------------------------------------------------------------------


def _board_order(store: SqlAlchemyTaskStore, tasks: list[str]) -> list[str]:
    """Board visual order for *tasks* (queue_rank desc, id desc)."""
    ranked = [(t.id, t.queue_rank) for t in (store.get(tid) for tid in tasks) if t]
    return [tid for tid, _ in sorted(ranked, key=lambda p: (p[1], p[0]), reverse=True)]


def _mk(store: SqlAlchemyTaskStore, seed: str, state: str = "idle") -> str:
    # Creation order assigns ascending ranks, so the board order starts as
    # the reverse of creation order.
    return store.create(
        task_id=_uid(seed),
        title=seed,
        goal="goal",
        owner_user_id="alice@example.com",
        state=state,
    ).id


def test_move_to_queue_end_live_card_parks_above_resolved_block(
    store: SqlAlchemyTaskStore,
) -> None:
    # Board: moved(live), a(live), res_2, res_1 — first resolved is res_2.
    res_1 = _mk(store, "res_1", state="agent-resolved")
    res_2 = _mk(store, "res_2", state="agent-resolved")
    a = _mk(store, "a")
    moved = _mk(store, "moved")
    assert _board_order(store, [moved, a, res_2, res_1]) == [moved, a, res_2, res_1]

    store.move_to_queue_end(moved)

    # moved parks directly above the FIRST resolved card (res_2), not under
    # the whole block. Adjacent ranks trigger the order-preserving tail
    # renumber; ranks stay unique.
    assert _board_order(store, [moved, a, res_2, res_1]) == [a, moved, res_2, res_1]
    ranks = [store.get(t).queue_rank for t in _board_order(store, [moved, a, res_2, res_1])]
    assert len(set(ranks)) == len(ranks)


def test_move_to_queue_end_free_slot_above_resolved_block(
    store: SqlAlchemyTaskStore,
) -> None:
    # A hole between the resolved block and the card above it (from a
    # deletion) is filled directly — no renumbering needed.
    res_1 = _mk(store, "res_1", state="agent-resolved")
    hole = _mk(store, "hole")
    live_a = _mk(store, "live_a")
    live_b = _mk(store, "live_b")
    store.delete(hole)
    assert _board_order(store, [live_b, live_a, res_1]) == [live_b, live_a, res_1]

    store.move_to_queue_end(live_b)

    assert _board_order(store, [live_b, live_a, res_1]) == [live_a, live_b, res_1]


def test_move_to_queue_end_already_above_resolved_is_stable(
    store: SqlAlchemyTaskStore,
) -> None:
    res_1 = _mk(store, "res_1", state="agent-resolved")
    moved = _mk(store, "moved")
    assert _board_order(store, [moved, res_1]) == [moved, res_1]

    # Already directly above the resolved block: rank is reused, order holds.
    store.move_to_queue_end(moved)
    assert _board_order(store, [moved, res_1]) == [moved, res_1]


def test_move_to_queue_end_interleaved_live_card_escapes_resolved_block(
    store: SqlAlchemyTaskStore,
) -> None:
    # A live card sitting between resolved cards moves to the top of the
    # resolved block (directly above the first resolved card).
    res_1 = _mk(store, "res_1", state="agent-resolved")
    moved = _mk(store, "moved")
    res_2 = _mk(store, "res_2", state="agent-resolved")
    assert _board_order(store, [moved, res_2, res_1]) == [res_2, moved, res_1]

    store.move_to_queue_end(moved)

    assert _board_order(store, [moved, res_2, res_1]) == [moved, res_2, res_1]


def test_move_to_queue_end_resolved_card_sinks_to_absolute_end(
    store: SqlAlchemyTaskStore,
) -> None:
    a = _mk(store, "a")
    b = _mk(store, "b")
    resolved = _mk(store, "res", state="agent-resolved")
    assert _board_order(store, [a, b, resolved]) == [resolved, b, a]

    store.move_to_queue_end(resolved)

    assert _board_order(store, [a, b, resolved]) == [b, a, resolved]


def test_move_to_queue_end_without_resolved_cards_sinks_to_absolute_end(
    store: SqlAlchemyTaskStore,
) -> None:
    a = _mk(store, "a")
    b = _mk(store, "b")
    assert _board_order(store, [a, b]) == [b, a]

    store.move_to_queue_end(a)

    assert _board_order(store, [a, b]) == [b, a]
