"""Tests for host-bound runner ensure helpers used by broker wakes."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from omnigent.server.routes import sessions as sessions_module
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


@pytest_asyncio.fixture
async def conv_store(tmp_path: Path) -> AsyncIterator[SqlAlchemyConversationStore]:
    """Per-test SQLite-backed conversation store."""
    store = SqlAlchemyConversationStore(f"sqlite:///{tmp_path / 'test.db'}")
    yield store


def _host_id(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.mark.asyncio
async def test_ensure_session_runner_client_returns_existing_client(
    conv_store: SqlAlchemyConversationStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connected runner is returned without launching a duplicate."""
    conv = conv_store.create_conversation(
        kind="default",
        title="broker",
        host_id=_host_id("host"),
        workspace="/tmp/ws",
    )
    sentinel = httpx.AsyncClient()
    launch = AsyncMock()

    async def _existing_client(session_id: str, runner_router: Any) -> httpx.AsyncClient:
        return sentinel

    monkeypatch.setattr(sessions_module, "_get_runner_client", _existing_client)
    monkeypatch.setattr(sessions_module, "_launch_runner_on_host", launch)

    client, needs_init = await sessions_module.ensure_session_runner_client(
        conv.id,
        conv,
        conversation_store=conv_store,
        runner_router=None,
    )
    assert client is sentinel
    assert needs_init is False
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_session_runner_client_launches_for_host_bound_session(
    conv_store: SqlAlchemyConversationStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host-bound sessions with no live runner trigger a host launch."""
    conv = conv_store.create_conversation(
        kind="default",
        title="broker",
        host_id=_host_id("host"),
        workspace="/tmp/ws",
    )
    sentinel = httpx.AsyncClient()
    launch = AsyncMock(
        return_value=sessions_module._HostLaunchAttempt(runner_id="runner_new"),
    )
    wait = AsyncMock(return_value=sentinel)
    lookups = 0

    async def _lookup_client(session_id: str, runner_router: Any) -> httpx.AsyncClient | None:
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else sentinel

    monkeypatch.setattr(sessions_module, "_get_runner_client", _lookup_client)
    monkeypatch.setattr(sessions_module, "_launch_runner_on_host", launch)
    monkeypatch.setattr(sessions_module, "_wait_for_runner_client", wait)

    class _FakeHostRegistry:
        def get(self, host_id: str) -> object:
            return object()

    infrastructure = sessions_module.ServerRunnerInfrastructure(
        host_registry=_FakeHostRegistry(),  # type: ignore[arg-type]
    )
    client, needs_init = await sessions_module.ensure_session_runner_client(
        conv.id,
        conv,
        conversation_store=conv_store,
        runner_router=None,
        infrastructure=infrastructure,
    )
    assert client is sentinel
    assert needs_init is True
    launch.assert_awaited_once()
    wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_host_runner_relaunch_preserves_content_updated_at(
    conv_store: SqlAlchemyConversationStore,
) -> None:
    """Runner rotation alone must not make a conversation appear unread."""
    conv = conv_store.create_conversation(
        kind="default",
        title="secretary",
        host_id=_host_id("host"),
        workspace="/tmp/ws",
    )

    class _DisconnectedRegistry:
        def send_text(self, host_conn: object, message: str) -> None:
            raise ConnectionError

    class _HostConnection:
        def __init__(self) -> None:
            self.pending_launches: dict[str, asyncio.Future[dict[str, str | None]]] = {}

    await sessions_module._launch_runner_on_host(
        conv,
        conv_store,
        _DisconnectedRegistry(),  # type: ignore[arg-type]
        _HostConnection(),  # type: ignore[arg-type]
    )

    updated = conv_store.get_conversation(conv.id)
    assert updated is not None
    assert updated.runner_id != conv.runner_id
    assert updated.updated_at == conv.updated_at


@pytest.mark.asyncio
async def test_wake_parent_launches_runner_before_dispatch(
    conv_store: SqlAlchemyConversationStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent wake ensures a runner exists instead of dropping immediately."""
    parent = conv_store.create_conversation(
        kind="default",
        title="broker",
        host_id=_host_id("host"),
        workspace="/tmp/ws",
    )
    child = conv_store.create_conversation(
        kind="sub_agent",
        title="child",
        parent_conversation_id=parent.id,
    )
    sentinel = httpx.AsyncClient()
    ensure = AsyncMock(return_value=(sentinel, True))
    init = AsyncMock()
    dispatched: list[str] = []
    fired = asyncio.Event()

    async def _record_dispatch(
        session_id: str,
        conv: Any,
        body: Any,
        conversation_store: SqlAlchemyConversationStore,
        runner_client: object,
        **kwargs: Any,
    ) -> str:
        dispatched.append(body.data["content"][0]["text"])
        fired.set()
        return "item_wake"

    monkeypatch.setattr(sessions_module, "ensure_session_runner_client", ensure)
    monkeypatch.setattr(sessions_module, "_ensure_runner_session_initialized", init)
    monkeypatch.setattr(sessions_module, "_dispatch_session_event_to_runner", _record_dispatch)

    ok = await sessions_module._wake_parent_for_blocked_child(
        parent.id,
        child,
        "[System: please triage and route these events]",
        conversation_store=conv_store,
        runner_router=None,
    )
    await asyncio.wait_for(fired.wait(), timeout=2.0)

    assert ok is True
    ensure.assert_awaited_once()
    init.assert_awaited_once()
    assert dispatched == ["[System: please triage and route these events]"]
