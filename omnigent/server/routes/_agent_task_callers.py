"""Identify the session behind an agent's call to the GlobalHub board routes."""

from __future__ import annotations

from fastapi import Request

from omnigent.entities import Conversation
from omnigent.runner.identity import (
    ORCHESTRATION_SOURCE_HEADER,
    RUNNER_TUNNEL_TOKEN_HEADER,
    token_bound_runner_id,
)
from omnigent.stores import ConversationStore
from omnigent.util.uuid import InvalidUuidError, uuid_to_bytes


def runner_bound_caller(request: Request, conversations: ConversationStore) -> Conversation | None:
    """Return the session that sent ``request`` through its runner, or ``None``. Blocking.

    The orchestration-source header names the session. Only that session's
    runner holds a tunnel token that maps to the session's ``runner_id``, so
    the board UI cannot speak for a session.
    """
    token = (request.headers.get(RUNNER_TUNNEL_TOKEN_HEADER) or "").strip()
    if not token:
        return None
    try:
        session_id = uuid_to_bytes(request.headers.get(ORCHESTRATION_SOURCE_HEADER, "")).hex()
    except InvalidUuidError:
        return None
    session = conversations.get_conversation(session_id)
    if session is None or session.runner_id != token_bound_runner_id(token):
        return None
    return session
