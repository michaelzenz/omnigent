"""Tests for human action task items (``kind="human_action"``)."""

from __future__ import annotations

import json
import uuid

import pytest

from omnigent.agent_tasks.event_types import HUMAN_ACTION_DONE_EVENT_TYPE
from omnigent.agent_tasks.items import (
    complete_human_action,
    create_task_item,
    patch_task_item,
    resolve_task_item,
)
from omnigent.errors import OmnigentError
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def stores(db_uri: str):
    return {
        "task": SqlAlchemyTaskStore(db_uri),
        "event": SqlAlchemyTaskEventStore(db_uri),
        "item": SqlAlchemyTaskItemStore(db_uri),
        "worker": SqlAlchemyWorkerStore(db_uri),
        "conversation": SqlAlchemyConversationStore(db_uri),
    }


def _task(stores, seed: str = "human-action-task"):
    task_store = stores["task"]
    task_store.create(_uid(seed), "Human action task", "goal", state="active")
    return task_store.get(_uid(seed))


def _human_item(stores, task, *, state: str = "pending", title: str = "Rotate the key"):
    return create_task_item(
        task=task,
        task_item_store=stores["item"],
        worker_store=stores["worker"],
        title=title,
        state=state,
        description="Only you have console access",
        kind="human_action",
    )


def test_create_human_action_round_trips_kind(stores) -> None:
    task = _task(stores)
    item = _human_item(stores, task)
    fetched = stores["item"].get_item(item.id)
    assert fetched is not None
    assert fetched.kind == "human_action"
    assert fetched.worker_id is None
    assert fetched.instructions is None


def test_create_item_defaults_to_work_kind(stores) -> None:
    task = _task(stores)
    item = create_task_item(
        task=task,
        task_item_store=stores["item"],
        worker_store=stores["worker"],
        title="Regular work",
    )
    assert item.kind == "work"


def test_create_human_action_rejects_worker(stores) -> None:
    task = _task(stores)
    with pytest.raises(OmnigentError, match="worker_id"):
        create_task_item(
            task=task,
            task_item_store=stores["item"],
            worker_store=stores["worker"],
            title="Bad",
            kind="human_action",
            worker_id=_uid("worker"),
        )


def test_create_human_action_rejects_instructions(stores) -> None:
    task = _task(stores)
    with pytest.raises(OmnigentError, match="description"):
        create_task_item(
            task=task,
            task_item_store=stores["item"],
            worker_store=stores["worker"],
            title="Bad",
            kind="human_action",
            instructions="do it yourself",
        )


def test_create_item_rejects_unknown_kind(stores) -> None:
    task = _task(stores)
    with pytest.raises(OmnigentError, match="Unknown task item kind"):
        create_task_item(
            task=task,
            task_item_store=stores["item"],
            worker_store=stores["worker"],
            title="Bad",
            kind="robot_action",
        )


def test_complete_human_action_marks_done_and_emits_routed_event(stores) -> None:
    task = _task(stores)
    item = _human_item(stores, task)

    updated = complete_human_action(
        item=item,
        task=task,
        task_item_store=stores["item"],
        task_event_store=stores["event"],
    )

    assert updated.state == "done"
    events = stores["event"].list_events(state="routed", task_id=task.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == HUMAN_ACTION_DONE_EVENT_TYPE
    assert event.source == "user"
    assert event.source_key == item.id
    assert event.routed_at is not None
    payload = json.loads(event.payload or "{}")
    assert payload == {"item_id": item.id, "item_title": item.title, "kind": "human_action"}


def test_complete_human_action_rejects_work_item(stores) -> None:
    task = _task(stores)
    item = create_task_item(
        task=task,
        task_item_store=stores["item"],
        worker_store=stores["worker"],
        title="Regular work",
        state="pending",
    )
    with pytest.raises(OmnigentError, match="human action"):
        complete_human_action(
            item=item,
            task=task,
            task_item_store=stores["item"],
            task_event_store=stores["event"],
        )


def test_complete_human_action_rejects_non_pending_state(stores) -> None:
    task = _task(stores)
    item = _human_item(stores, task, state="draft")
    with pytest.raises(OmnigentError, match="state 'draft'"):
        complete_human_action(
            item=item,
            task=task,
            task_item_store=stores["item"],
            task_event_store=stores["event"],
        )


def test_complete_human_action_still_done_when_event_emission_fails(stores, monkeypatch) -> None:
    task = _task(stores)
    item = _human_item(stores, task)

    def _boom(*args, **kwargs):
        raise RuntimeError("event store down")

    monkeypatch.setattr(stores["event"], "create_event", _boom)
    updated = complete_human_action(
        item=item,
        task=task,
        task_item_store=stores["item"],
        task_event_store=stores["event"],
    )
    assert updated.state == "done"


async def test_resolve_mark_done(stores) -> None:
    task = _task(stores)
    item = _human_item(stores, task)

    updated, execution = await resolve_task_item(
        item=item,
        resolution="mark_done",
        task=task,
        task_store=stores["task"],
        task_item_store=stores["item"],
        task_event_store=stores["event"],
        worker_store=stores["worker"],
        conversation_store=stores["conversation"],
    )

    assert updated.state == "done"
    assert execution is None


async def test_resolve_accept_rejects_human_action(stores) -> None:
    task = _task(stores)
    item = _human_item(stores, task)

    with pytest.raises(OmnigentError, match="marked done or dismissed"):
        await resolve_task_item(
            item=item,
            resolution="accept_item",
            task=task,
            task_store=stores["task"],
            task_item_store=stores["item"],
            task_event_store=stores["event"],
            worker_store=stores["worker"],
            conversation_store=stores["conversation"],
        )


async def test_resolve_reject_dismisses_human_action(stores) -> None:
    task = _task(stores)
    item = _human_item(stores, task)

    updated, execution = await resolve_task_item(
        item=item,
        resolution="reject_item",
        task=task,
        task_store=stores["task"],
        task_item_store=stores["item"],
        task_event_store=stores["event"],
        worker_store=stores["worker"],
        conversation_store=stores["conversation"],
    )

    assert updated.state == "cancelled"
    assert execution is None
    # Dismissal wakes no one: no routed event is emitted.
    assert stores["event"].list_events(state="routed", task_id=task.id) == []


def test_patch_task_item_rejects_instructions_and_worker_on_human_action(stores) -> None:
    task = _task(stores)
    item = _human_item(stores, task)
    # Force the item into an editable state so the kind guard is exercised.
    stores["item"].update_item(item.id, state="queued")
    item = stores["item"].get_item(item.id)
    assert item is not None

    with pytest.raises(OmnigentError, match="title and description"):
        patch_task_item(
            item=item,
            task_item_store=stores["item"],
            worker_store=stores["worker"],
            instructions="new steps",
        )
    with pytest.raises(OmnigentError, match="title and description"):
        patch_task_item(
            item=item,
            task_item_store=stores["item"],
            worker_store=stores["worker"],
            worker_id=_uid("worker"),
        )
