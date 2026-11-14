"""Tests for reporting finished session turns to GlobalHub (``agent_task_session_events``)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omnigent.agent_tasks.board_stores import open_board_stores
from omnigent.agent_tasks.role_sessions import RoleSessionStores
from omnigent.agent_tasks.session_labels import ROLE_LABEL
from omnigent.entities import Conversation, EventTag, TaskEvent
from omnigent.server.agent_task_session_events import SessionEventReporter, reports_to_globalhub
from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
PLACEMENT = GlobalHubPlacement(host_id=HOST_ID, workspace="/Users/alice/globalhub")


class _Hub:
    """Alice's GlobalHub over real stores, with a reporter."""

    def __init__(self, db_uri: str) -> None:
        self.conversations = SqlAlchemyConversationStore(db_uri)
        self.projects = SqlAlchemyProjectStore(db_uri)
        self.permissions = SqlAlchemyPermissionStore(db_uri)
        settings = UserSettingsStore(db_uri)
        settings.save_globalhub_placement(PLACEMENT, user_id=ALICE)
        board = open_board_stores(self.conversations)
        assert board is not None
        self.board = board
        self.reporter = SessionEventReporter(
            board=board,
            sessions=RoleSessionStores(
                conversation_store=self.conversations,
                agent_store=SqlAlchemyAgentStore(db_uri),
                user_settings_store=settings,
                project_store=self.projects,
                permission_store=self.permissions,
            ),
        )

    def session(self, owner: str = ALICE, **fields: Any) -> str:
        session = self.conversations.create_conversation(
            title="Fix S3 retries", host_id=HOST_ID, workspace="/Users/alice/s3-retries", **fields
        )
        self.permissions.ensure_user(owner)
        self.permissions.grant(owner, session.id, LEVEL_OWNER)
        return session.id

    def manager(self) -> str:
        return self.board.managers.create(
            uuid.uuid4().hex, owner_user_id=ALICE, title="Storage"
        ).id

    def attach(self, session_id: str, *, manager_id: str, state: str = "active") -> None:
        task = self.board.tasks.create(
            uuid.uuid4().hex,
            owner_user_id=ALICE,
            title="S3 retries",
            goal="Fix them",
            manager_id=manager_id,
            state=state,
        )
        self.board.workers.create(
            uuid.uuid4().hex, owner_user_id=ALICE, task_id=task.id, session_id=session_id
        )

    def reports(self) -> list[TaskEvent]:
        return self.board.events.list(owner_user_id=ALICE, event_type="session.turn.finished")

    def deletions(self) -> list[TaskEvent]:
        return self.board.events.list(owner_user_id=ALICE, event_type="session.deleted")

    def conv(self, session_id: str) -> Conversation:
        conv = self.conversations.get_conversation(session_id)
        assert conv is not None
        return conv


@pytest.fixture()
def hub(db_uri: str) -> _Hub:
    return _Hub(db_uri)


def test_a_session_on_no_task_reports_a_finished_turn_to_the_broker_once(hub: _Hub) -> None:
    project_id = hub.projects.create(uuid.uuid4().hex, "Storage", ALICE).id
    session_id = hub.session(project_id=project_id)

    hub.reporter.report_turn_finished(session_id)
    hub.reporter.report_turn_finished(session_id)

    [event] = hub.reports()
    assert (event.state, event.manager_id, event.source_session_id) == (
        "unrouted",
        None,
        session_id,
    )
    assert (event.title, event.tags) == (
        "Session turn finished: Fix S3 retries",
        [EventTag(tag_type="host", tag=HOST_ID)],
    )
    assert event.payload == {
        "session_id": session_id,
        "session_title": "Fix S3 retries",
        "project_name": "Storage",
        "status": "idle",
    }


def test_a_session_on_live_tasks_reports_to_each_of_their_managers_once(hub: _Hub) -> None:
    session_id = hub.session()
    storage, compute, former = hub.manager(), hub.manager(), hub.manager()
    hub.attach(session_id, manager_id=storage)
    hub.attach(session_id, manager_id=compute, state="idle")
    hub.attach(session_id, manager_id=storage, state="agent-resolved")
    hub.attach(session_id, manager_id=former, state="archived")

    hub.reporter.report_turn_finished(session_id)
    hub.reporter.report_turn_finished(session_id)

    reports = hub.reports()
    assert sorted(event.manager_id or "" for event in reports) == sorted([storage, compute])
    assert {(event.state, len(event.tags)) for event in reports} == {("routed", 0)}


def test_a_session_whose_tasks_are_all_finished_reports_to_the_broker(hub: _Hub) -> None:
    session_id = hub.session()
    hub.attach(session_id, manager_id=hub.manager(), state="archived")

    hub.reporter.report_turn_finished(session_id)

    [event] = hub.reports()
    assert (event.state, event.manager_id) == ("unrouted", None)


@pytest.mark.parametrize(
    ("fields", "reports"),
    [
        ({}, True),
        ({"labels": {ROLE_LABEL: "task_broker"}}, False),
        ({"kind": "sub_agent"}, False),
        ({"side_chat_parent_id": "c0"}, False),
    ],
    ids=["user_session", "role_session", "sub_agent", "side_chat"],
)
def test_role_sessions_sub_agents_and_side_chats_do_not_report(
    fields: dict[str, Any], reports: bool
) -> None:
    conv = Conversation(id="c1", created_at=0, updated_at=0, root_conversation_id="c1", **fields)

    assert reports_to_globalhub(conv) is reports


def test_a_user_without_a_globalhub_host_reports_nothing(hub: _Hub) -> None:
    session_id = hub.session(BOB)

    hub.reporter.report_turn_finished(session_id)

    assert hub.board.events.list(owner_user_id=BOB) == []


async def test_only_a_finished_turn_is_reported_in_the_background(hub: _Hub) -> None:
    session_id = hub.session()

    hub.reporter.turn_ended(session_id, "failed")
    await hub.reporter.stop()
    after_a_failed_turn = hub.reports()
    hub.reporter.turn_ended(session_id, "idle")
    await hub.reporter.stop()

    assert (after_a_failed_turn, len(hub.reports())) == ([], 1)


def test_deleting_a_session_on_live_tasks_tells_their_managers_and_detaches_it(
    hub: _Hub,
) -> None:
    session_id = hub.session()
    storage, former = hub.manager(), hub.manager()
    hub.attach(session_id, manager_id=storage)
    hub.attach(session_id, manager_id=former, state="archived")

    hub.reporter.report_session_deleted(hub.conv(session_id))
    hub.reporter.report_session_deleted(hub.conv(session_id))

    [event] = hub.deletions()
    assert (event.state, event.manager_id, event.title) == (
        "routed",
        storage,
        "Session deleted: Fix S3 retries",
    )
    assert event.payload == {
        "session_id": session_id,
        "session_title": "Fix S3 retries",
        "project_name": None,
    }
    assert hub.board.workers.list_for_session(session_id, owner_user_id=ALICE) == []


@pytest.mark.parametrize("holder", ["nobody", "broker", "manager"])
def test_deleting_a_session_on_no_task_tells_whoever_holds_its_turn_report(
    hub: _Hub, holder: str
) -> None:
    session_id = hub.session()
    manager_id = hub.manager()
    if holder != "nobody":
        hub.reporter.report_turn_finished(session_id)
    if holder == "manager":
        [turn] = hub.reports()
        hub.board.events.route_to_manager([turn.id], owner_user_id=ALICE, manager_id=manager_id)

    hub.reporter.report_session_deleted(hub.conv(session_id))

    expected = {
        "nobody": [],
        "broker": [("unrouted", None)],
        "manager": [("routed", manager_id)],
    }[holder]
    assert [(event.state, event.manager_id) for event in hub.deletions()] == expected


def test_deleting_a_role_session_drops_its_queue_items_and_frees_their_events(
    hub: _Hub,
) -> None:
    session_id = hub.session(labels={ROLE_LABEL: "task_broker"})
    comment = hub.board.events.create(
        uuid.uuid4().hex, owner_user_id=ALICE, event_type="github.pr.comment", title="Comment"
    )
    hub.board.queue.enqueue(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        target_session_id=session_id,
        kind="events",
        event_ids=[comment.id],
    )

    hub.reporter.report_session_deleted(hub.conv(session_id))

    freed = hub.board.events.get(comment.id, owner_user_id=ALICE)
    assert hub.board.queue.list(owner_user_id=ALICE) == []
    assert freed is not None and freed.queue_item_id is None
    assert hub.deletions() == []
