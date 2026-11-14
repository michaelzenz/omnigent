"""Tests for sending GlobalHub messages into role sessions (``agent_task_delivery``)."""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server import agent_task_delivery
from omnigent.server.agent_task_delivery import (
    SessionSendDeps,
    send_to_session,
    settle_orphaned_turns,
)
from omnigent.server.routes import sessions
from omnigent.server.routes._sessions import helpers
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

OWNER = "alice@example.com"
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


class _Runner:
    """Stands in for the runner path a send takes, recording each dispatch."""

    def __init__(self, *, reachable: bool = True, reply: Callable[[], Any] | None = None) -> None:
        self.reachable = reachable
        self.reply = reply or (lambda: SimpleNamespace(accepted=True))
        self.dispatched: list[tuple[Any, dict[str, Any]]] = []

    async def connect(self, *, conv: Any, **_: Any) -> tuple[object | None, Any]:
        return (object() if self.reachable else None), conv

    async def initialize(self, *_: Any) -> None:
        return None

    async def dispatch(self, *args: Any, **kwargs: Any) -> Any:
        self.dispatched.append((args[2], kwargs))
        return self.reply()


@pytest.fixture()
def deps(db_uri: str) -> SessionSendDeps:
    return SessionSendDeps(
        conversation_store=SqlAlchemyConversationStore(db_uri),
        agent_store=SqlAlchemyAgentStore(db_uri),
        app_state=SimpleNamespace(),
    )


def _use(monkeypatch: pytest.MonkeyPatch, runner: _Runner) -> _Runner:
    monkeypatch.setattr(sessions, "ensure_runner_connected", runner.connect)
    monkeypatch.setattr(sessions, "_ensure_runner_session_initialized", runner.initialize)
    monkeypatch.setattr(sessions, "_dispatch_session_event_to_runner", runner.dispatch)
    return runner


def _session(deps: SessionSendDeps, monkeypatch: pytest.MonkeyPatch, status: str) -> str:
    session = deps.conversation_store.create_conversation(title="Task broker")
    monkeypatch.setitem(sessions._session_status_cache, session.id, status)
    return session.id


@pytest.mark.parametrize("status", ["idle", "failed"])
async def test_a_session_between_turns_gets_the_message_from_its_owner(
    deps: SessionSendDeps, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    runner = _use(monkeypatch, _Runner())
    session_id = _session(deps, monkeypatch, status)

    result = await send_to_session(session_id, "2 new events", created_by=OWNER, deps=deps)

    assert result.outcome == "sent"
    [(event, options)] = runner.dispatched
    assert (event.type, event.data["content"]) == (
        "message",
        [{"type": "input_text", "text": "2 new events"}],
    )
    assert options["created_by"] == OWNER


@pytest.mark.parametrize("status", ["running", "waiting"])
async def test_a_session_mid_turn_is_busy(
    deps: SessionSendDeps, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    runner = _use(monkeypatch, _Runner())
    session_id = _session(deps, monkeypatch, status)

    result = await send_to_session(session_id, "notice", created_by=OWNER, deps=deps)

    assert (result.outcome, runner.dispatched) == ("busy", [])


async def test_sends_that_cannot_reach_the_session_say_why(
    deps: SessionSendDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> Any:
        raise OmnigentError("Runner rejected the message", code=ErrorCode.RUNNER_UNAVAILABLE)

    archived = _session(deps, monkeypatch, "idle")
    deps.conversation_store.update_conversation(archived, archived=True)
    session_id = _session(deps, monkeypatch, "idle")

    async def send() -> tuple[str, str | None]:
        result = await send_to_session(session_id, "notice", created_by=OWNER, deps=deps)
        return result.outcome, result.error

    _use(monkeypatch, _Runner())
    missing = await send_to_session(f"{999:032x}", "notice", created_by=OWNER, deps=deps)
    gone = await send_to_session(archived, "notice", created_by=OWNER, deps=deps)
    _use(monkeypatch, _Runner(reachable=False))
    unreachable = await send()
    _use(monkeypatch, _Runner(reply=refuse))
    rejected = await send()
    _use(monkeypatch, _Runner(reply=lambda: SimpleNamespace(accepted=False)))
    not_accepted = await send()

    assert (missing.outcome, gone.outcome) == ("gone", "gone")
    assert unreachable == ("unreachable", "no runner can be reached or woken")
    assert rejected == ("failed", "Runner rejected the message")
    assert not_accepted == ("failed", "the runner did not accept the message")


def test_a_finished_or_failed_turn_tells_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    notified: list[str] = []
    monkeypatch.setattr(agent_task_delivery, "notify_turn_end", notified.append)
    monkeypatch.setattr(helpers.session_live_state, "persist_live_status", lambda *_: None)
    session_id = "c0ffee00c0ffee00c0ffee00c0ffee01"
    monkeypatch.delitem(helpers._session_status_cache, session_id, raising=False)

    helpers._publish_status(session_id, "idle")
    helpers._publish_status(session_id, "running")
    helpers._publish_status(session_id, "waiting")
    before_the_turn_ends = list(notified)
    helpers._publish_status(session_id, "idle")
    helpers._publish_status(session_id, "running")
    helpers._publish_status(session_id, "failed")

    assert before_the_turn_ends == []
    assert notified == [session_id, session_id]


def _running_session(deps: SessionSendDeps, *, fresh_heartbeat: bool) -> str:
    """Seed a session that the database reads as mid-turn, bound to a runner."""
    agent_id = uuid.uuid4().hex
    deps.agent_store.create(agent_id, f"agent-{agent_id}", "bundles/agent.tar.gz")
    session = deps.conversation_store.create_conversation(agent_id=agent_id, host_id=HOST_ID)
    runner_id = f"runner_{session.id}"
    assert deps.conversation_store.set_runner_id(session.id, runner_id)
    deps.conversation_store.set_session_live_status(session.id, "running")
    if fresh_heartbeat:
        deps.conversation_store.touch_runner_liveness([runner_id], int(time.time()))
    return session.id


def _with_liveness(
    deps: SessionSendDeps, *, runner_online: bool, host_online: bool
) -> SessionSendDeps:
    liveness = helpers.SessionLiveness(runner_online=runner_online, host_online=host_online)
    return dataclasses.replace(deps, liveness_lookup=lambda ids: dict.fromkeys(ids, liveness))


async def _host_asleep(*_: Any, **__: Any) -> bool:
    return True


@pytest.mark.parametrize(
    ("fresh_heartbeat", "seen_here", "runner_online", "host_online", "settled"),
    [
        (False, False, False, True, True),
        (True, False, False, True, False),
        (False, True, False, True, False),
        (False, False, True, True, False),
        (False, False, False, False, False),
    ],
    ids=["runner_gone", "fresh_heartbeat", "status_seen_here", "runner_elsewhere", "host_asleep"],
)
async def test_only_a_turn_whose_runner_is_gone_is_settled(
    deps: SessionSendDeps,
    monkeypatch: pytest.MonkeyPatch,
    fresh_heartbeat: bool,
    seen_here: bool,
    runner_online: bool,
    host_online: bool,
    settled: bool,
) -> None:
    session_id = _running_session(deps, fresh_heartbeat=fresh_heartbeat)
    if seen_here:
        monkeypatch.setitem(sessions._session_status_cache, session_id, "running")
    monkeypatch.setattr(sessions, "_host_row_offline", _host_asleep)
    checking = _with_liveness(deps, runner_online=runner_online, host_online=host_online)
    session = deps.conversation_store.get_conversation(session_id)
    assert session is not None

    result = await settle_orphaned_turns([session], deps=checking)

    stored = deps.conversation_store.get_conversation(session_id)
    assert stored is not None
    expected = ([session_id], "idle") if settled else ([], "running")
    assert (result, stored.live_status) == expected


async def test_a_send_settles_a_turn_whose_runner_is_gone_and_goes_out(
    deps: SessionSendDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _use(monkeypatch, _Runner())
    session_id = _running_session(deps, fresh_heartbeat=False)
    checking = _with_liveness(deps, runner_online=False, host_online=True)

    result = await send_to_session(session_id, "notice", created_by=OWNER, deps=checking)

    assert (result.outcome, len(runner.dispatched)) == ("sent", 1)
