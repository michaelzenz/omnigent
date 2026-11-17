"""Tests for each user's GlobalHub role sessions."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid

import pytest

from omnigent.agent_tasks.role_sessions import (
    GLOBALHUB_PROJECT_NAME,
    RoleSessionStores,
    create_role_session,
    ensure_globalhub_project,
    ensure_role_session,
    ensure_watcher_session,
    is_role_session_current,
)
from omnigent.agent_tasks.roles import (
    BROKER_ROLE_KEY,
    MANAGER_ROLE_KEY,
    BuiltinRole,
    builtin_role,
)
from omnigent.agent_tasks.session_labels import BROKER_ROLE_VALUE, ROLE_LABEL, WATCHER_ROLE_VALUE
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore

ALICE = "alice@example.com"
PLACEMENT = GlobalHubPlacement(
    host_id="0f1e2d3c4b5a69788796a5b4c3d2e1f0", workspace="/Users/alice/globalhub"
)


@pytest.fixture()
def stores(db_uri: str) -> RoleSessionStores:
    agent_store = SqlAlchemyAgentStore(db_uri)
    for name in ("onih-pmv2", "onih-custodian", "onih-watcher"):
        agent_store.create(uuid.uuid4().hex, name, f"bundles/{name}.tar.gz")
    return RoleSessionStores(
        conversation_store=SqlAlchemyConversationStore(db_uri),
        agent_store=agent_store,
        user_settings_store=UserSettingsStore(db_uri),
        project_store=SqlAlchemyProjectStore(db_uri),
        permission_store=SqlAlchemyPermissionStore(db_uri),
    )


def _broker() -> BuiltinRole:
    role = builtin_role(BROKER_ROLE_KEY)
    assert role is not None
    return role


async def test_ensure_creates_one_owned_session_per_user_and_role(
    stores: RoleSessionStores,
) -> None:
    settings = stores.user_settings_store
    settings.save_globalhub_placement(PLACEMENT, user_id=ALICE)
    settings.save_globalhub_role_overrides(
        BROKER_ROLE_KEY, {"model": "example-model"}, user_id=ALICE
    )

    first, second = await asyncio.gather(
        ensure_role_session(_broker(), user_id=ALICE, stores=stores),
        ensure_role_session(_broker(), user_id=ALICE, stores=stores),
    )

    (session, _), (same_session, _) = first, second
    assert same_session.id == session.id
    assert sorted([first[1], second[1]]) == [False, True]
    assert (session.host_id, session.workspace, session.title) == (
        PLACEMENT.host_id,
        PLACEMENT.workspace,
        "Task broker",
    )
    assert session.labels[ROLE_LABEL] == BROKER_ROLE_VALUE
    assert session.model_override == "example-model"
    broker_agent = stores.agent_store.get_by_name("onih-pmv2")
    assert broker_agent is not None and session.agent_id == broker_agent.id
    assert session.project_id == ensure_globalhub_project(stores.project_store, ALICE)
    assert stores.conversation_store.get_session_owner(session.id, owner_only=True) == ALICE


@pytest.mark.parametrize("change", ["deleted", "archived", "moved"])
async def test_ensure_replaces_a_session_the_user_can_no_longer_use(
    stores: RoleSessionStores, change: str
) -> None:
    settings = stores.user_settings_store
    settings.save_globalhub_placement(PLACEMENT, user_id=ALICE)
    old, _ = await ensure_role_session(_broker(), user_id=ALICE, stores=stores)
    if change == "deleted":
        await stores.conversation_store.delete_conversation(old.id)
    elif change == "archived":
        stores.conversation_store.update_conversation(old.id, archived=True)
    else:
        moved = GlobalHubPlacement(host_id=PLACEMENT.host_id, workspace="/Users/alice/elsewhere")
        settings.save_globalhub_placement(moved, user_id=ALICE)

    new, created = await ensure_role_session(_broker(), user_id=ALICE, stores=stores)

    assert created is True
    assert new.id != old.id
    assert settings.get_globalhub_role_session(BROKER_ROLE_KEY, user_id=ALICE) == new.id
    expected_workspace = "/Users/alice/elsewhere" if change == "moved" else PLACEMENT.workspace
    assert new.workspace == expected_workspace


async def test_only_a_roles_current_session_takes_its_messages(
    stores: RoleSessionStores, db_uri: str
) -> None:
    settings = stores.user_settings_store
    settings.save_globalhub_placement(PLACEMENT, user_id=ALICE)
    broker, _ = await ensure_role_session(_broker(), user_id=ALICE, stores=stores)
    manager_role = builtin_role(MANAGER_ROLE_KEY)
    assert manager_role is not None
    owned, unowned = [
        create_role_session(
            manager_role, user_id=ALICE, placement=PLACEMENT, title=title, stores=stores
        )
        for title in ("Storage", "Former manager")
    ]
    managers = SqlAlchemyManagerStore(db_uri)
    managers.create(
        uuid.uuid4().hex, owner_user_id=ALICE, title="Storage", conversation_id=owned.id
    )
    plain = stores.conversation_store.create_conversation(title="Fix S3 retries")

    def current(session_id: str, user_id: str | None = ALICE) -> bool:
        session = stores.conversation_store.get_conversation(session_id)
        assert session is not None
        return is_role_session_current(session, user_id=user_id, stores=stores, managers=managers)

    before = [current(session.id) for session in (broker, owned, unowned, plain)]
    for_bob = current(broker.id, user_id="bob@example.com")
    stores.conversation_store.update_conversation(owned.id, archived=True)
    moved = GlobalHubPlacement(host_id=PLACEMENT.host_id, workspace="/Users/alice/elsewhere")
    settings.save_globalhub_placement(moved, user_id=ALICE)

    assert before == [True, True, False, False]
    assert for_bob is False
    assert (current(broker.id), current(owned.id)) == (False, False)


async def test_a_watcher_gets_its_own_session_until_it_is_archived(
    stores: RoleSessionStores, db_uri: str
) -> None:
    watchers = SqlAlchemyWatcherStore(db_uri)
    watcher = watchers.create(
        uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher", model="example-model"
    )
    stores.user_settings_store.save_globalhub_placement(PLACEMENT, user_id=ALICE)

    first, second = await asyncio.gather(
        ensure_watcher_session(watcher.id, user_id=ALICE, stores=stores, watchers=watchers),
        ensure_watcher_session(watcher.id, user_id=ALICE, stores=stores, watchers=watchers),
    )
    (session, _), (same_session, _) = first, second
    stores.conversation_store.update_conversation(session.id, archived=True)
    replaced, created = await ensure_watcher_session(
        watcher.id, user_id=ALICE, stores=stores, watchers=watchers
    )
    with pytest.raises(OmnigentError) as missing:
        await ensure_watcher_session(
            watcher.id, user_id="bob@example.com", stores=stores, watchers=watchers
        )

    assert same_session.id == session.id
    assert sorted([first[1], second[1]]) == [False, True]
    assert (session.title, session.labels[ROLE_LABEL], session.model_override) == (
        "PR watcher",
        WATCHER_ROLE_VALUE,
        "example-model",
    )
    assert created is True and replaced.id != session.id
    recorded = watchers.get(watcher.id, owner_user_id=ALICE)
    assert recorded is not None and recorded.conversation_id == replaced.id
    assert missing.value.code == ErrorCode.NOT_FOUND


async def test_only_a_watchers_current_session_takes_its_runs(
    stores: RoleSessionStores, db_uri: str
) -> None:
    watchers = SqlAlchemyWatcherStore(db_uri)
    managers = SqlAlchemyManagerStore(db_uri)
    watcher = watchers.create(uuid.uuid4().hex, owner_user_id=ALICE, name="PR watcher")
    stores.user_settings_store.save_globalhub_placement(PLACEMENT, user_id=ALICE)
    session, _ = await ensure_watcher_session(
        watcher.id, user_id=ALICE, stores=stores, watchers=watchers
    )

    def current(store: SqlAlchemyWatcherStore | None) -> bool:
        loaded = stores.conversation_store.get_conversation(session.id)
        assert loaded is not None
        return is_role_session_current(
            loaded, user_id=ALICE, stores=stores, managers=managers, watchers=store
        )

    before, without_store = current(watchers), current(None)
    watchers.delete(watcher.id, owner_user_id=ALICE)

    assert (before, without_store, current(watchers)) == (True, False, False)


async def test_ensure_requires_a_placement_and_a_singleton_role(
    stores: RoleSessionStores,
) -> None:
    with pytest.raises(OmnigentError) as unplaced:
        await ensure_role_session(_broker(), user_id=ALICE, stores=stores)
    manager = builtin_role(MANAGER_ROLE_KEY)
    assert manager is not None
    with pytest.raises(ValueError, match="not one per user"):
        await ensure_role_session(manager, user_id=ALICE, stores=stores)

    assert unplaced.value.code == ErrorCode.INVALID_INPUT
    assert (
        stores.user_settings_store.get_globalhub_role_session(BROKER_ROLE_KEY, user_id=ALICE)
        is None
    )


def test_globalhub_project_is_created_once_per_user(stores: RoleSessionStores) -> None:
    project_id = ensure_globalhub_project(stores.project_store, ALICE)

    assert ensure_globalhub_project(stores.project_store, ALICE) == project_id
    assert ensure_globalhub_project(stores.project_store, "bob@example.com") != project_id
    assert [project.name for project in stores.project_store.list(user_id=ALICE)] == [
        GLOBALHUB_PROJECT_NAME
    ]
    assert ensure_globalhub_project(None, ALICE) is None


def test_the_first_role_session_announces_the_new_globalhub_project(
    stores: RoleSessionStores,
) -> None:
    """Open apps learn about the project, so they can show the session inside it."""
    announced: list[str | None] = []
    announcing = dataclasses.replace(stores, on_project_created=announced.append)
    manager = builtin_role(MANAGER_ROLE_KEY)
    assert manager is not None

    first = create_role_session(
        manager, user_id=ALICE, placement=PLACEMENT, title="First", stores=announcing
    )
    second = create_role_session(
        manager, user_id=ALICE, placement=PLACEMENT, title="Second", stores=announcing
    )

    assert announced == [ALICE]
    assert first.project_id == second.project_id
    assert first.project_id == ensure_globalhub_project(stores.project_store, ALICE)
