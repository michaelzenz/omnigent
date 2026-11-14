"""Tests for sending GlobalHub messages into role sessions (``agent_task_delivery``)."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.agent_task_delivery import SessionSendDeps, send_to_session
from omnigent.server.routes import sessions
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

OWNER = "alice@example.com"


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
