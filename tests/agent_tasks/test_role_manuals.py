"""Tests for the instructions and context attached to each turn of a GlobalHub role session."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from omnigent.agent_tasks.role_manuals import role_turn_context, role_turn_instructions
from omnigent.agent_tasks.roles import (
    BROKER_ROLE_KEY,
    MANAGER_ROLE_KEY,
    WATCHER_ROLE,
    BuiltinRole,
    builtin_role,
)
from omnigent.entities import Manager
from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.user_settings_store import UserSettingsStore
from omnigent.stores.watcher_store.sqlalchemy_store import SqlAlchemyWatcherStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
SESSION_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
OTHER_SESSION_ID = "2d3c4b5a69788796a5b4c3d2e1f00f1e"


def _manager() -> BuiltinRole:
    role = builtin_role(MANAGER_ROLE_KEY)
    assert role is not None
    return role


def _conversation_store(db_uri: str, owner: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        storage_location=db_uri,
        get_session_owner=lambda _session_id, *, owner_only: owner,
    )


def test_turn_instructions_use_the_session_owners_manual(db_uri: str) -> None:
    settings = UserSettingsStore(db_uri)
    settings.save_globalhub_role_overrides(
        MANAGER_ROLE_KEY, {"manual": "Alice's manual."}, user_id=ALICE
    )
    settings.save_globalhub_role_overrides(
        MANAGER_ROLE_KEY, {"manual": "Local manual."}, user_id=None
    )

    def instructions(owner: str | None) -> str:
        return role_turn_instructions(
            _manager(), SESSION_ID, _conversation_store(db_uri, owner), None
        )

    assert instructions(ALICE) == "Alice's manual."
    # Single-user sessions have no owner grant and use the local user's settings.
    assert instructions(None) == "Local manual."
    assert instructions(BOB) == _manager().default_manual()


def test_turn_instructions_keep_request_instructions_after_the_manual(db_uri: str) -> None:
    conversation_store = _conversation_store(db_uri, ALICE)
    manual = _manager().default_manual()

    assert role_turn_instructions(_manager(), SESSION_ID, conversation_store, "  ") == manual
    assert (
        role_turn_instructions(_manager(), SESSION_ID, conversation_store, "Be brief.")
        == f"{manual}\n\nBe brief."
    )
    # Without SQL storage there are no saved overrides to read.
    assert role_turn_instructions(_manager(), SESSION_ID, object(), None) == manual


def test_a_managers_turns_name_its_session_and_manager_ids(db_uri: str) -> None:
    manager_id = "1e2d3c4b5a69788796a5b4c3d2e1f00f"
    SqlAlchemyManagerStore(db_uri).create(
        manager_id, owner_user_id=ALICE, title="Storage", conversation_id=SESSION_ID
    )
    broker = builtin_role(BROKER_ROLE_KEY)
    assert broker is not None
    manual = _manager().default_manual()
    ids = f"Your session id is {SESSION_ID}. Your manager id is {manager_id}."

    def instructions(role: BuiltinRole, owner: str, request: str | None = None) -> str:
        return role_turn_instructions(
            role, SESSION_ID, _conversation_store(db_uri, owner), request
        )

    assert instructions(_manager(), ALICE, "Be brief.") == f"{manual}\n\n{ids}\n\nBe brief."
    # Another user's manager row, and other roles, add no ids.
    assert instructions(_manager(), BOB) == manual
    assert instructions(broker, ALICE) == broker.default_manual()


def test_a_managers_turn_context_is_its_record_and_live_roster(db_uri: str) -> None:
    managers, tasks = SqlAlchemyManagerStore(db_uri), SqlAlchemyTaskStore(db_uri)
    storage = managers.create(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        title="Storage",
        description="S3 client retries.\nRoute storage events here.",
        conversation_id=SESSION_ID,
    )
    docs = managers.create(
        uuid.uuid4().hex,
        owner_user_id=ALICE,
        title="Docs",
        conversation_id=OTHER_SESSION_ID,
        host_id="3c4b5a69788796a5b4c3d2e1f00f1e2d",
        workspace="/srv/docs",
    )

    def task(title: str, **fields: str) -> str:
        return tasks.create(
            uuid.uuid4().hex,
            owner_user_id=ALICE,
            title=title,
            manager_id=storage.id,
            **fields,
        ).id

    def record(manager: Manager, *placement: str) -> list[str]:
        def utc(epoch_seconds: int) -> str:
            return datetime.fromtimestamp(epoch_seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        return [
            f"Manager: {manager.title}",
            f"Manager id: {manager.id}",
            f"Session id: {manager.conversation_id}",
            *placement,
            f"Owner: {ALICE}",
            f"Created: {utc(manager.created_at)}; updated: {utc(manager.updated_at)}",
        ]

    task("Old work", state="archived")
    plain = task("Docs page")
    noted = task("S3 retries", internal_note="PR 12 in example-org/repo.\nWaits for review.")
    broker = builtin_role(BROKER_ROLE_KEY)
    assert broker is not None

    def context(role: BuiltinRole, owner: str, session_id: str = SESSION_ID) -> str | None:
        return role_turn_context(role, session_id, _conversation_store(db_uri, owner))

    assert context(_manager(), ALICE) == "\n".join(
        [
            "[Task roster]",
            *record(
                storage,
                "Description: S3 client retries.\n  Route storage events here.",
                "Host: your GlobalHub host",
                "Workspace: your GlobalHub workspace",
            ),
            "Live tasks: 2 (full at 30)",
            f"{noted} — S3 retries (active)",
            "  Note: PR 12 in example-org/repo.",
            "  Waits for review.",
            f"{plain} — Docs page (active)",
        ]
    )
    assert context(_manager(), ALICE, OTHER_SESSION_ID) == "\n".join(
        [
            "[Task roster]",
            *record(
                docs,
                "Description: (none yet)",
                "Host: 3c4b5a69788796a5b4c3d2e1f00f1e2d",
                "Workspace: /srv/docs",
            ),
            "Live tasks: 0 (full at 30)",
        ]
    )
    # Another user's manager row, and other roles, get no context.
    assert context(_manager(), BOB) is None
    assert context(broker, ALICE) is None


def test_a_watchers_turns_carry_its_own_manual_else_the_template(db_uri: str) -> None:
    watchers = SqlAlchemyWatcherStore(db_uri)
    watcher = watchers.create(
        "1e2d3c4b5a69788796a5b4c3d2e1f00f",
        owner_user_id=ALICE,
        name="PR watcher",
        manual="Watch the release.",
    )
    watchers.update(watcher.id, owner_user_id=ALICE, conversation_id=SESSION_ID)
    template = WATCHER_ROLE.default_manual()
    ids = f"You are the watcher 'PR watcher'. Your watcher id is {watcher.id}."

    def instructions(owner: str) -> str:
        return role_turn_instructions(
            WATCHER_ROLE, SESSION_ID, _conversation_store(db_uri, owner), None
        )

    custom = instructions(ALICE)
    watchers.update(watcher.id, owner_user_id=ALICE, manual=None)
    default = instructions(ALICE)

    assert custom == f"Watch the release.\n\n{ids}"
    assert default == f"{template}\n\n{ids}"
    # Another user's session, or a deleted watcher's, gets the template alone.
    assert instructions(BOB) == template
    watchers.delete(watcher.id, owner_user_id=ALICE)
    assert instructions(ALICE) == template
