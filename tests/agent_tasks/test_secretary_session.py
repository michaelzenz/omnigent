"""Tests for secretary profile/session bootstrap helpers."""

from __future__ import annotations

import uuid

import pytest

from omnigent._wrapper_labels import CLAUDE_NATIVE_WRAPPER_VALUE, UI_MODE_LABEL_KEY, UI_MODE_TERMINAL_VALUE, WRAPPER_LABEL_KEY
from omnigent.agent_tasks.secretary_session import (
    NO_HOST_AVAILABLE_MESSAGE,
    apply_secretary_session_labels,
    get_or_create_secretary_profile,
)
from omnigent.db.utils import generate_agent_id
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.secretary_profile_store.sqlalchemy_store import SqlAlchemySecretaryProfileStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def test_get_or_create_secretary_profile_uses_first_live_host(db_uri: str) -> None:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-secretary", bundle_location="test:///bundle")
    host_store = HostStore(db_uri)
    host_id = _uid("secretary_host")
    host_store.upsert_on_connect(host_id, "secretary-host", RESERVED_USER_LOCAL)
    profile_store = SqlAlchemySecretaryProfileStore(db_uri)

    profile = get_or_create_secretary_profile(
        profile_user_id="__anonymous__",
        auth_user_id=None,
        secretary_profile_store=profile_store,
        host_store=host_store,
        agent_store=agent_store,
    )

    assert profile.host_id == host_id
    assert profile.agent_id == agent_id


def test_get_or_create_secretary_profile_fails_without_live_host(db_uri: str) -> None:
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-secretary", bundle_location="test:///bundle")
    profile_store = SqlAlchemySecretaryProfileStore(db_uri)

    with pytest.raises(OmnigentError) as exc_info:
        get_or_create_secretary_profile(
            profile_user_id="__anonymous__",
            auth_user_id=None,
            secretary_profile_store=profile_store,
            host_store=HostStore(db_uri),
            agent_store=agent_store,
        )

    assert exc_info.value.code == ErrorCode.INVALID_INPUT
    assert str(exc_info.value) == NO_HOST_AVAILABLE_MESSAGE


def test_apply_secretary_session_labels_for_claude_native(db_uri: str) -> None:
    conversation_store = SqlAlchemyConversationStore(db_uri)
    conversation = conversation_store.create_conversation(title="secretary")
    apply_secretary_session_labels(
        conversation_store,
        conversation.id,
        harness="claude-native",
    )
    refreshed = conversation_store.get_conversation(conversation.id)
    assert refreshed is not None
    labels = refreshed.labels
    assert labels[UI_MODE_LABEL_KEY] == UI_MODE_TERMINAL_VALUE
    assert labels[WRAPPER_LABEL_KEY] == CLAUDE_NATIVE_WRAPPER_VALUE
