"""Tests for the GlobalHub queue dispatcher."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Collection, Iterable, Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.agent_tasks.board_stores import open_board_stores
from omnigent.agent_tasks.role_sessions import RoleSessionStores, ensure_role_session
from omnigent.agent_tasks.roles import BROKER_ROLE_KEY, builtin_role
from omnigent.db.db_models import current_workspace_id, workspace_scope
from omnigent.entities import AgentQueueItem, Conversation
from omnigent.server.agent_queue_dispatcher import AgentQueueDispatcher, Packager
from omnigent.server.agent_task_delivery import SendResult, SessionSendDeps
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE = "alice@example.com"
PLACEMENT = GlobalHubPlacement(
    host_id="0f1e2d3c4b5a69788796a5b4c3d2e1f0", workspace="/Users/alice/globalhub"
)
NOW = 1_800_000_000


class _Harness:
    """A dispatcher over real stores, with a settable clock and fake sends and orphan checks."""

    def __init__(self, db_uri: str, packagers: Sequence[Packager] = ()) -> None:
        conversations = SqlAlchemyConversationStore(db_uri)
        agents = SqlAlchemyAgentStore(db_uri)
        self.agents = agents
        self.install_agents()
        self.sessions = RoleSessionStores(
            conversation_store=conversations,
            agent_store=agents,
            user_settings_store=UserSettingsStore(db_uri),
            project_store=SqlAlchemyProjectStore(db_uri),
            permission_store=SqlAlchemyPermissionStore(db_uri),
        )
        self.sessions.user_settings_store.save_globalhub_placement(PLACEMENT, user_id=ALICE)
        board = open_board_stores(conversations)
        assert board is not None
        self.board = board
        self.now = NOW
        self.replies: list[SendResult] = []
        self.sent: list[tuple[str, str, str | None]] = []
        # The workspace each send ran in.
        self.sent_workspaces: list[int] = []
        # Sessions whose turn lost its runner.
        self.lost_turns: set[str] = set()
        self.dispatcher = AgentQueueDispatcher(
            board=board,
            sessions=self.sessions,
            send_deps=SessionSendDeps(
                conversation_store=conversations, agent_store=agents, app_state=SimpleNamespace()
            ),
            renderers={"events": lambda item: item.payload.get("text")},
            packagers=packagers,
            send=self._send,
            settle_orphans=self._settle_orphans,
            clock=lambda: self.now,
        )

    async def _send(
        self, session_id: str, text: str, *, created_by: str | None, deps: Any
    ) -> SendResult:
        self.sent.append((session_id, text, created_by))
        self.sent_workspaces.append(current_workspace_id())
        return self.replies.pop(0) if self.replies else SendResult("sent")

    def install_agents(self) -> None:
        """Install the GlobalHub role agents in the current workspace."""
        for name in ("onih-pmv2", "onih-custodian"):
            self.agents.create(uuid.uuid4().hex, name, f"bundles/{name}.tar.gz")

    async def _settle_orphans(
        self, conversations: Iterable[Conversation], *, deps: Any
    ) -> list[str]:
        return [conv.id for conv in conversations if conv.id in self.lost_turns]

    async def broker(self) -> str:
        role = builtin_role(BROKER_ROLE_KEY)
        assert role is not None
        session, _ = await ensure_role_session(role, user_id=ALICE, stores=self.sessions)
        return session.id

    def enqueue(
        self, session_id: str, text: str | None = "2 new events", event_ids: Collection[str] = ()
    ) -> str:
        item = self.board.queue.enqueue(
            uuid.uuid4().hex,
            owner_user_id=ALICE,
            target_session_id=session_id,
            kind="events",
            payload={"text": text} if text is not None else None,
            event_ids=event_ids,
        )
        assert item is not None
        return item.id

    def item(self, item_id: str) -> AgentQueueItem | None:
        return self.board.queue.get(item_id, owner_user_id=ALICE)

    def state(self, item_id: str) -> str | None:
        item = self.item(item_id)
        return item.state if item is not None else None

    async def run(self) -> None:
        await asyncio.gather(*await self.dispatcher.run_once())


async def _until(condition: Callable[[], bool]) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


@pytest.fixture()
def harness(db_uri: str) -> _Harness:
    return _Harness(db_uri)


async def test_a_session_gets_its_next_item_only_after_its_turn_ends(harness: _Harness) -> None:
    broker = await harness.broker()
    first, second = harness.enqueue(broker, "first"), harness.enqueue(broker, "second")

    await harness.run()
    await harness.run()
    sent_while_the_turn_runs = list(harness.sent)
    harness.dispatcher.turn_ended(broker, "idle")
    await _until(lambda: harness.state(first) == "done")
    await harness.run()

    assert sent_while_the_turn_runs == [(broker, "first", ALICE)]
    assert harness.sent[1:] == [(broker, "second", ALICE)]
    assert harness.state(second) == "sent"


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (SendResult("busy"), ("queued", 0, NOW + 5, None)),
        (SendResult("unreachable", "no runner"), ("queued", 1, NOW + 30, "no runner")),
    ],
    ids=["busy", "unreachable"],
)
async def test_a_busy_session_is_rechecked_soon_and_a_failed_send_backs_off(
    harness: _Harness, reply: SendResult, expected: tuple[Any, ...]
) -> None:
    broker = await harness.broker()
    item_id = harness.enqueue(broker)
    harness.replies.append(reply)

    await harness.run()

    item = harness.item(item_id)
    assert item is not None
    assert (item.state, item.attempts, item.not_before, item.last_error) == expected


async def test_items_without_a_current_target_or_anything_to_say_are_dropped(
    harness: _Harness,
) -> None:
    plain = harness.sessions.conversation_store.create_conversation(title="Fix S3 retries")
    event_id = uuid.uuid4().hex
    harness.board.events.create(
        event_id, owner_user_id=ALICE, event_type="github.pr.comment", title="Comment"
    )
    stranded = harness.enqueue(plain.id, event_ids=[event_id])
    empty = harness.enqueue(await harness.broker(), text=None)

    await harness.run()

    assert harness.sent == []
    assert (harness.item(stranded), harness.item(empty)) == (None, None)
    event = harness.board.events.get(event_id, owner_user_id=ALICE)
    assert event is not None and event.queue_item_id is None


async def test_a_missed_turn_end_is_settled_after_a_minute_idle(harness: _Harness) -> None:
    item_id = harness.enqueue(await harness.broker())
    await harness.run()

    harness.now = NOW + 30
    await harness.run()
    harness.now = NOW + 89
    await harness.run()
    after_59_idle_seconds = harness.state(item_id)
    harness.now = NOW + 90
    await harness.run()

    assert (after_59_idle_seconds, harness.state(item_id)) == ("sent", "done")


async def test_a_session_whose_turn_lost_its_runner_takes_its_next_item_at_once(
    harness: _Harness,
) -> None:
    broker = await harness.broker()
    first, second = harness.enqueue(broker, "first"), harness.enqueue(broker, "second")
    await harness.run()
    harness.lost_turns.add(broker)

    await harness.run()

    assert harness.sent == [(broker, "first", ALICE), (broker, "second", ALICE)]
    assert (harness.state(first), harness.state(second)) == ("done", "sent")


async def test_packagers_run_every_five_seconds(db_uri: str) -> None:
    runs: list[int] = []

    class _Recorder:
        async def package(self, now: int) -> None:
            runs.append(now)

    harness = _Harness(db_uri, packagers=[_Recorder()])
    for offset in (0, 4, 5):
        harness.now = NOW + offset
        await harness.run()

    assert runs == [NOW, NOW + 5]


async def test_a_claim_left_by_a_crashed_send_goes_back_after_ten_minutes(
    harness: _Harness,
) -> None:
    broker = await harness.broker()
    harness.enqueue(broker)
    harness.board.queue.claim_due_all_workspaces(now=NOW)

    harness.now = NOW + 599
    await harness.run()
    sent_early = list(harness.sent)
    harness.now = NOW + 659
    await harness.run()

    assert sent_early == []
    assert harness.sent == [(broker, "2 new events", ALICE)]


async def test_an_item_in_another_workspace_is_sent_and_settled_in_that_workspace(
    harness: _Harness,
) -> None:
    with workspace_scope(7):
        harness.install_agents()
        harness.sessions.user_settings_store.save_globalhub_placement(PLACEMENT, user_id=ALICE)
        broker = await harness.broker()
        item_id = harness.enqueue(broker, "from workspace 7")

    # The loop itself runs outside any workspace, as it does in the server.
    await harness.run()
    with workspace_scope(7):
        sent = harness.state(item_id)
    harness.now = NOW + 30
    await harness.run()
    harness.now = NOW + 90
    await harness.run()

    assert (harness.sent, harness.sent_workspaces) == ([(broker, "from workspace 7", ALICE)], [7])
    with workspace_scope(7):
        assert (sent, harness.state(item_id)) == ("sent", "done")


async def test_a_claim_left_by_a_stopped_server_goes_back_when_the_dispatcher_starts(
    harness: _Harness,
) -> None:
    broker = await harness.broker()
    harness.enqueue(broker)
    harness.board.queue.claim_due_all_workspaces(now=NOW)

    await harness.dispatcher.start()
    await harness.dispatcher.stop()
    await harness.run()

    assert harness.sent == [(broker, "2 new events", ALICE)]
