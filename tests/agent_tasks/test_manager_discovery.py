"""Tests for manager discovery and attach-or-create bootstrap."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.bootstrap import bootstrap_task_manager
from omnigent.agent_tasks.manager_discovery import (
    list_active_managers,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import Task
from omnigent.errors import OmnigentError
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def discovery_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    manager_store = SqlAlchemyManagerStore(db_uri)
    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_agent_id,
        host_id=_uid("host_a"),
        workspace="/tmp/mgr",
    )
    manager = manager_store.upsert(
        _uid("mgr_a"),
        owner_user_id="user-1",
        role_key="manager:uploads",
        description="Owns S3 upload reliability.",
        conversation_id=manager_conv.id,
        title="Task manager: Owns S3 upload reliability.",
        host_id=_uid("host_a"),
        workspace="/tmp/mgr",
        harness="openai-agents",
        agent_profile_id=manager_agent_id,
    )
    return {
        "agent_store": agent_store,
        "task_store": task_store,
        "conversation_store": conversation_store,
        "manager_store": manager_store,
        "manager": manager,
        "manager_conv": manager_conv,
        "manager_agent_id": manager_agent_id,
    }


def _role_profile(agent_id: str):
    from omnigent.entities.task_role_profile import TaskRoleProfile

    return TaskRoleProfile(
        role="manager:default",
        kind="manager",
        agent_profile_id=agent_id,
        created_at=0,
        host_id="host-x",
        workspace="~/",
    )


def _create_task(
    store: SqlAlchemyTaskStore,
    seed: str,
    *,
    title: str = "A task",
    goal: str = "a goal",
    owner: str = "user-1",
    manager_id: str | None = None,
    state: str = "active",
) -> Task:
    return store.create(
        _uid(seed),
        title,
        goal,
        owner_user_id=owner,
        manager_id=manager_id,
        state=state,
    )


def _probe(seed: str, *, title: str = "probe", goal: str = "probe goal") -> Task:
    return Task(
        id=_uid(seed),
        manager_role_key="manager:default",
        owner_user_id="user-1",
        title=title,
        description=None,
        internal_note=None,
        state="active",
        created_at=1,
        goal=goal,
    )


def _managers(setup: dict, owner: str = "user-1"):
    return list_active_managers(
        owner_user_id=owner,
        manager_store=setup["manager_store"],
        task_store=setup["task_store"],
    )


# ── list_active_managers ───────────────────────────────────────────


def test_list_active_managers_groups_tasks_per_manager(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    manager_id = discovery_setup["manager"].id
    _create_task(task_store, "t1", title="S3 uploads", manager_id=manager_id)
    _create_task(task_store, "t2", title="S3 retries", manager_id=manager_id)
    _create_task(task_store, "t3", title="No manager")

    managers = _managers(discovery_setup)
    assert len(managers) == 1
    assert managers[0].conversation_id == discovery_setup["manager_conv"].id
    assert managers[0].task_count == 2
    assert managers[0].host_id == _uid("host_a")
    assert managers[0].role_key == "manager:uploads"
    assert managers[0].description == "Owns S3 upload reliability."


def test_list_active_managers_scopes_by_owner(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    manager_id = discovery_setup["manager"].id
    _create_task(task_store, "t_mine", manager_id=manager_id)
    _create_task(task_store, "t_theirs", owner="user-2", manager_id=manager_id)

    mine = _managers(discovery_setup, "user-1")
    theirs = _managers(discovery_setup, "user-2")
    assert len(mine) == 1
    assert theirs == []
    assert {t.id for t in mine[0].tasks} == {_uid("t_mine"), _uid("t_theirs")}


def test_list_active_managers_includes_registered_manager_with_zero_tasks(
    discovery_setup: dict,
) -> None:
    managers = _managers(discovery_setup)

    assert len(managers) == 1
    assert managers[0].conversation_id == discovery_setup["manager_conv"].id
    assert managers[0].task_count == 0
    assert managers[0].tasks == []


# ── bootstrap attach-or-create ─────────────────────────────────────


async def test_bootstrap_returns_when_manager_already_live(discovery_setup: dict) -> None:
    """Idempotent: a task whose manager session still exists is returned as-is."""
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    manager_id = discovery_setup["manager"].id
    task = _create_task(task_store, "t_bound", manager_id=manager_id)

    async def _no_spawn(**kwargs):
        raise AssertionError("already live; should not spawn")

    class _State:
        manager_store = discovery_setup["manager_store"]

    updated = await bootstrap_task_manager(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        session_creator=_no_spawn,
        app_state=_State(),
        user_id="user-1",
    )
    assert updated.manager_id == manager_id


@pytest.mark.asyncio
async def test_bootstrap_heals_dead_session_from_row_snapshot(
    discovery_setup: dict,
) -> None:
    """A dead session pointer heals from the manager row's own snapshot.

    Same durable manager id, fresh session pointer — no task or role-profile
    input needed.
    """
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    task = _create_task(
        task_store,
        "t_stale",
        title="S3 uploads",
        manager_id=discovery_setup["manager"].id,
    )
    await conversation_store.delete_conversation(discovery_setup["manager_conv"].id)

    spawned: list[str] = []

    async def _spawn(*, body, request, user_id, **kwargs):
        conv = conversation_store.create_conversation(
            title=body.title,
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
        )
        spawned.append(conv.id)
        return conv

    class _State:
        manager_store = discovery_setup["manager_store"]

    updated = await bootstrap_task_manager(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        session_creator=_spawn,
        app_state=_State(),
        user_id="user-1",
    )
    assert updated.manager_id == discovery_setup["manager"].id
    assert len(spawned) == 1
    healed = discovery_setup["manager_store"].get(discovery_setup["manager"].id)
    assert healed is not None
    assert healed.conversation_id == spawned[0]
    assert healed.agent_profile_id == discovery_setup["manager"].agent_profile_id


@pytest.mark.asyncio
async def test_bootstrap_throws_when_task_has_no_manager(discovery_setup: dict) -> None:
    """Manager-first architecture: an unattached task is an invariant violation."""
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    task = _create_task(task_store, "t_orphan")

    class _State:
        manager_store = discovery_setup["manager_store"]

    with pytest.raises(OmnigentError, match="has no manager"):
        await bootstrap_task_manager(
            task=task,
            task_store=task_store,
            conversation_store=conversation_store,
            session_creator=None,
            app_state=_State(),
            user_id="user-1",
        )


@pytest.mark.asyncio
async def test_bootstrap_throws_when_manager_row_missing(discovery_setup: dict) -> None:
    """A dangling manager_id is an invariant violation, not a heal case."""
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    task = _create_task(task_store, "t_dangling", manager_id=_uid("ghost-manager"))

    class _State:
        manager_store = discovery_setup["manager_store"]

    with pytest.raises(OmnigentError, match="does not exist"):
        await bootstrap_task_manager(
            task=task,
            task_store=task_store,
            conversation_store=conversation_store,
            session_creator=None,
            app_state=_State(),
            user_id="user-1",
        )


def test_list_active_managers_filters_incomplete_snapshots(
    discovery_setup: dict,
) -> None:
    """A manager row missing required snapshot fields is skipped, not defaulted."""
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    manager_store: SqlAlchemyManagerStore = discovery_setup["manager_store"]
    _create_task(task_store, "t_ok", manager_id=discovery_setup["manager"].id)

    # Registered without host — cannot re-create a session, so not listable.
    broken = manager_store.upsert(
        _uid("mgr_no_host"),
        owner_user_id="user-1",
        role_key="manager:default",
        description="Broken snapshot.",
        conversation_id=_uid("session-broken"),
        title="Task manager: Broken snapshot.",
        workspace="/tmp/broken",
        harness="openai-agents",
        agent_profile_id=discovery_setup["manager_agent_id"],
    )
    _create_task(task_store, "t_broken", manager_id=broken.id)

    managers = _managers(discovery_setup)
    assert [manager.manager_id for manager in managers] == [discovery_setup["manager"].id]
    assert managers[0].task_count == 1
