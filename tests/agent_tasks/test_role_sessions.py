"""Tests for each user's GlobalHub role sessions."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from omnigent.agent_tasks.role_sessions import (
    GLOBALHUB_PROJECT_NAME,
    RoleSessionStores,
    ensure_globalhub_project,
    ensure_role_session,
)
from omnigent.agent_tasks.roles import (
    BROKER_ROLE_KEY,
    MANAGER_ROLE_KEY,
    BuiltinRole,
    builtin_role,
)
from omnigent.agent_tasks.session_labels import BROKER_ROLE_VALUE, ROLE_LABEL
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE = "alice@example.com"
PLACEMENT = GlobalHubPlacement(
    host_id="0f1e2d3c4b5a69788796a5b4c3d2e1f0", workspace="/Users/alice/globalhub"
)


@pytest.fixture()
def stores(db_uri: str) -> RoleSessionStores:
    agent_store = SqlAlchemyAgentStore(db_uri)
    for name in ("onih-pmv2", "onih-custodian"):
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
