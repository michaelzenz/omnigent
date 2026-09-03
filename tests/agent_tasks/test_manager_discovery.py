"""Tests for manager discovery and attach-or-create bootstrap."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from omnigent.agent_tasks.bootstrap import bootstrap_task_manager, resolve_bootstrap_params
from omnigent.agent_tasks.manager_discovery import (
    choose_manager_for_task,
    list_active_managers,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import Task
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
    manager_store.upsert(
        manager_conv.id,
        owner_user_id="user-1",
        role_key="manager:uploads",
        description="Owns S3 upload reliability.",
    )
    return {
        "agent_store": agent_store,
        "task_store": task_store,
        "conversation_store": conversation_store,
        "manager_store": manager_store,
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
    manager_conversation_id: str | None = None,
    state: str = "active",
) -> Task:
    return store.create(
        _uid(seed),
        title,
        goal,
        owner_user_id=owner,
        manager_conversation_id=manager_conversation_id,
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
        conversation_store=setup["conversation_store"],
    )


# ── list_active_managers ───────────────────────────────────────────


def test_list_active_managers_groups_tasks_per_manager(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conv_id = discovery_setup["manager_conv"].id
    _create_task(task_store, "t1", title="S3 uploads", manager_conversation_id=conv_id)
    _create_task(task_store, "t2", title="S3 retries", manager_conversation_id=conv_id)
    _create_task(task_store, "t3", title="No manager")

    managers = _managers(discovery_setup)
    assert len(managers) == 1
    assert managers[0].conversation_id == conv_id
    assert managers[0].task_count == 2
    assert managers[0].host_id == _uid("host_a")
    assert managers[0].role_key == "manager:uploads"
    assert managers[0].description == "Owns S3 upload reliability."


def test_list_active_managers_scopes_by_owner(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conv_id = discovery_setup["manager_conv"].id
    _create_task(task_store, "t_mine", manager_conversation_id=conv_id)
    _create_task(task_store, "t_theirs", owner="user-2", manager_conversation_id=conv_id)

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


# ── choose_manager_for_task ────────────────────────────────────────


def test_choose_prefers_relevant_manager(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    s3_conv = discovery_setup["manager_conv"]
    billing_conv = conversation_store.create_conversation(
        title="Billing manager",
        agent_id=discovery_setup["manager_agent_id"],
        host_id=_uid("host_a"),
        workspace="/tmp/mgr_b",
    )
    discovery_setup["manager_store"].upsert(
        billing_conv.id,
        owner_user_id="user-1",
        role_key="manager:billing",
        description="Owns billing exports.",
    )
    _create_task(task_store, "t_s3", title="S3 uploads", manager_conversation_id=s3_conv.id)
    _create_task(
        task_store, "t_bill", title="Billing export", manager_conversation_id=billing_conv.id
    )
    managers = _managers(discovery_setup)
    assert len(managers) == 2

    probe = _probe("probe", title="S3 retry storms", goal="stop s3 retry storms")
    chosen = choose_manager_for_task(managers, probe=probe, host_id=_uid("host_a"))
    assert chosen is not None
    assert chosen.conversation_id == s3_conv.id


def test_choose_respects_host_compatibility(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conv_id = discovery_setup["manager_conv"].id  # host-a
    _create_task(task_store, "t_host", manager_conversation_id=conv_id)
    managers = _managers(discovery_setup)

    probe = _probe("probe_host")
    assert choose_manager_for_task(managers, probe=probe, host_id=_uid("host_b")) is None
    assert choose_manager_for_task(managers, probe=probe, host_id=_uid("host_a")) is not None


def test_choose_respects_capacity(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conv_id = discovery_setup["manager_conv"].id
    _create_task(task_store, "t_cap", manager_conversation_id=conv_id)
    managers = _managers(discovery_setup)

    probe = _probe("probe_cap")
    assert choose_manager_for_task(managers, probe=probe, host_id=_uid("host_a"), capacity=1) is None
    assert (
        choose_manager_for_task(managers, probe=probe, host_id=_uid("host_a"), capacity=2)
        is not None
    )


# ── bootstrap attach-or-create ─────────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_attaches_to_existing_manager(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    conv_id = discovery_setup["manager_conv"].id  # host-a
    _create_task(task_store, "t_existing", manager_conversation_id=conv_id)

    new_task = _create_task(
        task_store, "t_new", title="S3 uploads", goal="reliable s3 uploads"
    )
    params = resolve_bootstrap_params(
        host_id=_uid("host_a"),
        workspace="~/",
        harness=None,
        model=None,
        role_profile=_role_profile(discovery_setup["manager_agent_id"]),
    )

    async def _no_spawn(**kwargs):
        raise AssertionError("should attach, not spawn")

    updated = await bootstrap_task_manager(
        task=new_task,
        task_store=task_store,
        conversation_store=conversation_store,
        params=params,
        session_creator=_no_spawn,
        app_state=None,
        user_id="user-1",
    )
    assert updated.manager_conversation_id == conv_id


@pytest.mark.asyncio
async def test_bootstrap_spawns_when_no_compatible_manager(discovery_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    conv_id = discovery_setup["manager_conv"].id  # host-a
    _create_task(task_store, "t_existing", manager_conversation_id=conv_id)

    new_task = _create_task(task_store, "t_new2", title="Something", goal="something")
    params = resolve_bootstrap_params(
        host_id=_uid("host_b"),  # incompatible with the only manager
        workspace="~/",
        harness=None,
        model=None,
        role_profile=_role_profile(discovery_setup["manager_agent_id"]),
    )
    spawned: dict = {}

    async def _spawn(*, body, request, user_id, **kwargs):
        spawned["host_id"] = body.host_id
        return conversation_store.create_conversation(
            title=body.title,
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
        )

    class _State:
        pass

    updated = await bootstrap_task_manager(
        task=new_task,
        task_store=task_store,
        conversation_store=conversation_store,
        params=params,
        session_creator=_spawn,
        app_state=_State(),
        user_id="user-1",
    )
    assert spawned["host_id"] == _uid("host_b")
    assert updated.manager_conversation_id is not None
    assert updated.manager_conversation_id != conv_id


@pytest.mark.asyncio
async def test_bootstrap_returns_when_manager_already_live(discovery_setup: dict) -> None:
    """Idempotent: a task whose manager session still exists is returned as-is."""
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    conv_id = discovery_setup["manager_conv"].id
    task = _create_task(task_store, "t_bound", manager_conversation_id=conv_id)

    async def _no_spawn(**kwargs):
        raise AssertionError("already bound; should not spawn")

    updated = await bootstrap_task_manager(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        params=None,  # unused on the idempotent path
        session_creator=_no_spawn,
        app_state=None,
        user_id="user-1",
    )
    assert updated.manager_conversation_id == conv_id


@pytest.mark.asyncio
async def test_bootstrap_reattaches_when_manager_gone(discovery_setup: dict) -> None:
    """A dead stored manager session self-heals into a fresh attach."""
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    live_conv_id = discovery_setup["manager_conv"].id
    # The task points at a deleted session; a live manager exists to attach to.
    task = _create_task(
        task_store,
        "t_stale",
        title="S3 uploads",
        manager_conversation_id=_uid("dead_conv"),
    )
    _create_task(task_store, "t_anchor", manager_conversation_id=live_conv_id)

    params = resolve_bootstrap_params(
        host_id=_uid("host_a"),
        workspace="~/",
        harness=None,
        model=None,
        role_profile=_role_profile(discovery_setup["manager_agent_id"]),
    )

    async def _no_spawn(**kwargs):
        raise AssertionError("should re-attach to the live manager, not spawn")

    updated = await bootstrap_task_manager(
        task=task,
        task_store=task_store,
        conversation_store=conversation_store,
        params=params,
        session_creator=_no_spawn,
        app_state=None,
        user_id="user-1",
    )
    assert updated.manager_conversation_id == live_conv_id


@pytest.mark.asyncio
async def test_concurrent_bootstraps_spawn_one_manager(discovery_setup: dict) -> None:
    """Cold-start race: two simultaneous bootstraps yield exactly one manager.

    Without the per-owner lock both coroutines read an empty roster while the
    first is still mid-spawn, and both spawn a manager session.
    """
    task_store: SqlAlchemyTaskStore = discovery_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = discovery_setup["conversation_store"]
    task_a = _create_task(task_store, "t_race_a", title="First", goal="first goal")
    task_b = _create_task(task_store, "t_race_b", title="Second", goal="second goal")
    params = resolve_bootstrap_params(
        host_id=_uid("host_b"),
        workspace="~/",
        harness=None,
        model=None,
        role_profile=_role_profile(discovery_setup["manager_agent_id"]),
    )
    spawns: list[str] = []

    async def _spawn(*, body, request, user_id, **kwargs):
        conv = conversation_store.create_conversation(
            title=body.title,
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
        )
        # Yield like the real create_session_internal does, so the second
        # bootstrap gets a chance to run while the first is mid-spawn.
        await asyncio.sleep(0)
        spawns.append(conv.id)
        return conv

    class _State:
        pass

    updated_a, updated_b = await asyncio.gather(
        bootstrap_task_manager(
            task=task_a,
            task_store=task_store,
            conversation_store=conversation_store,
            params=params,
            session_creator=_spawn,
            app_state=_State(),
            user_id="user-1",
        ),
        bootstrap_task_manager(
            task=task_b,
            task_store=task_store,
            conversation_store=conversation_store,
            params=params,
            session_creator=_spawn,
            app_state=_State(),
            user_id="user-1",
        ),
    )
    assert len(spawns) == 1
    assert updated_a.manager_conversation_id == spawns[0]
    assert updated_b.manager_conversation_id == spawns[0]
