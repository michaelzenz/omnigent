"""Tests for the GlobalHub system notice routes (``/v1/agent-tasks/system-notice``)."""

from __future__ import annotations

from typing import Any

import httpx

from omnigent.runner.identity import (
    ORCHESTRATION_SOURCE_HEADER,
    RUNNER_TUNNEL_TOKEN_HEADER,
    token_bound_runner_id,
)
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

ALICE = {"X-Forwarded-Email": "alice@example.com"}
BOB = {"X-Forwarded-Email": "bob@example.com"}
ROUTE = "/v1/agent-tasks/system-notice"
CLEANUP = {
    "title": "Stale session cleanup",
    "body": "Toggle the sessions to delete.",
    "actions": [
        {
            "id": "confirm_cleanup",
            "label": "Delete selected sessions",
            "inputs": [
                {"id": "delete_a", "kind": "toggle", "group": "Host one", "default": False},
                {"id": "reason", "kind": "text", "placeholder": "Why delete them?"},
            ],
        }
    ],
}


def _action(action_id: str, input_ids: list[str], **fields: Any) -> dict[str, Any]:
    inputs = [{"id": input_id, "kind": "toggle"} for input_id in input_ids]
    return {"id": action_id, "inputs": inputs, **fields}


async def test_the_caller_has_one_active_notice_until_they_dismiss_it(
    auth_client: httpx.AsyncClient,
) -> None:
    empty = await auth_client.get(ROUTE, headers=ALICE)
    replaced = await auth_client.post(ROUTE, json={"title": "Old cleanup"}, headers=ALICE)
    published = await auth_client.post(ROUTE, json=CLEANUP, headers=ALICE)
    active = (await auth_client.get(ROUTE, headers=ALICE)).json()["notice"]
    bobs = await auth_client.get(ROUTE, headers=BOB)
    dismissed = await auth_client.post(f"{ROUTE}/dismiss", headers=ALICE)
    after = await auth_client.get(ROUTE, headers=ALICE)
    again = await auth_client.post(f"{ROUTE}/dismiss", headers=ALICE)

    assert empty.json() == {"object": "agent.task.board.system_notice", "notice": None}
    assert active["id"] == published.json()["notice"]["id"] != replaced.json()["notice"]["id"]
    assert (active["state"], active["body_kind"], active["publisher_session_id"]) == (
        "active",
        "markdown",
        None,
    )
    toggle, text = active["actions"][0]["inputs"]
    assert (toggle["group"], toggle["default"], text["placeholder"]) == (
        "Host one",
        False,
        "Why delete them?",
    )
    assert bobs.json()["notice"] is None
    assert (dismissed.json()["notice"]["id"], dismissed.json()["notice"]["state"]) == (
        active["id"],
        "resolved",
    )
    assert (after.json()["notice"], again.json()["notice"]) == (None, None)


async def test_only_a_sessions_own_runner_names_it_as_the_publisher(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    conversations = SqlAlchemyConversationStore(db_uri)
    session = conversations.create_conversation(title="Task custodian")
    runner_token = "custodian-runner"
    conversations.set_runner_id(session.id, token_bound_runner_id(runner_token))
    own_runner = {
        ORCHESTRATION_SOURCE_HEADER: session.id,
        RUNNER_TUNNEL_TOKEN_HEADER: runner_token,
    }

    confirmed = await auth_client.post(ROUTE, json=CLEANUP, headers={**ALICE, **own_runner})
    unconfirmed = await auth_client.post(
        ROUTE,
        json=CLEANUP,
        headers={**ALICE, **own_runner, RUNNER_TUNNEL_TOKEN_HEADER: "another-runner"},
    )

    assert confirmed.json()["notice"]["publisher_session_id"] == session.id
    assert confirmed.json()["notice"]["publisher_label"] == "Task custodian"
    unconfirmed_notice = unconfirmed.json()["notice"]
    assert (
        unconfirmed_notice["publisher_session_id"],
        unconfirmed_notice["publisher_label"],
    ) == (None, None)


async def test_publishing_checks_declared_ids_and_size_but_not_counts(
    auth_client: httpx.AsyncClient,
) -> None:
    rejected = [
        {"title": " "},
        {"title": "Cleanup", "actions": [_action("confirm", []), _action("confirm", [])]},
        {"title": "Cleanup", "actions": [_action("confirm", ["delete_a", "delete_a"])]},
        {"title": "Cleanup", "actions": [{"id": "confirm", "inputs": [{"id": "a", "kind": "x"}]}]},
        {"title": "Cleanup", "actions": [_action("confirm", [], label="x" * 2 * 1024 * 1024)]},
    ]
    # Far past v2's 16 actions of 16 inputs; input ids repeat across actions.
    many = [_action(f"action_{n}", [f"input_{m}" for m in range(40)]) for n in range(40)]

    statuses = [
        (await auth_client.post(ROUTE, json=body, headers=ALICE)).status_code for body in rejected
    ]
    accepted = await auth_client.post(
        ROUTE, json={"title": "Many", "actions": many}, headers=ALICE
    )

    assert statuses == [422] * len(rejected)
    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()["notice"]["actions"]) == 40
