"""Tests for the GlobalHub system notice routes (``/v1/agent-tasks/system-notice``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.agent_tasks.roles import CUSTODIAN_ROLE_KEY
from omnigent.runner.identity import (
    ORCHESTRATION_SOURCE_HEADER,
    RUNNER_TUNNEL_TOKEN_HEADER,
    token_bound_runner_id,
)
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE_ID = "alice@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
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


def _place_globalhub_with_a_custodian(db_uri: str) -> None:
    SqlAlchemyAgentStore(db_uri).create(
        uuid.uuid4().hex, "onih-custodian", "bundles/onih-custodian.tar.gz"
    )
    UserSettingsStore(db_uri).save_globalhub_placement(
        GlobalHubPlacement(
            host_id="0f1e2d3c4b5a69788796a5b4c3d2e1f0", workspace="/Users/alice/globalhub"
        ),
        user_id=ALICE_ID,
    )


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


async def test_a_click_waits_in_the_queue_for_the_custodian(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _place_globalhub_with_a_custodian(db_uri)
    notice = (await auth_client.post(ROUTE, json=CLEANUP, headers=ALICE)).json()["notice"]

    clicked = await auth_client.post(
        f"{ROUTE}/actions/confirm_cleanup/click",
        json={"inputs": {"delete_a": True, "reason": "Finished last week"}},
        headers=ALICE,
    )

    assert clicked.json() == {
        "object": "agent.task.system_notice_click",
        "notice_id": notice["id"],
        "action_id": "confirm_cleanup",
        "inputs": {"delete_a": True, "reason": "Finished last week"},
    }
    custodian = UserSettingsStore(db_uri).get_globalhub_role_session(
        CUSTODIAN_ROLE_KEY, user_id=ALICE_ID
    )
    [item] = SqlAlchemyAgentQueueStore(db_uri).list(owner_user_id=ALICE_ID)
    assert (item.kind, item.target_session_id, item.payload["inputs"]) == (
        "notice_click",
        custodian,
        {"delete_a": True, "reason": "Finished last week"},
    )
    still_active = (await auth_client.get(ROUTE, headers=ALICE)).json()["notice"]
    assert (still_active["id"], still_active["state"]) == (notice["id"], "active")


async def test_a_click_must_match_the_declared_action_and_its_inputs(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _place_globalhub_with_a_custodian(db_uri)
    nothing_active = await auth_client.post(
        f"{ROUTE}/actions/confirm/click", json={}, headers=ALICE
    )
    actions = [
        {
            "id": "confirm",
            "inputs": [
                {"id": "delete_a", "kind": "toggle"},
                {"id": "reason", "kind": "text", "required": True},
            ],
        },
        {"id": "bulk", "inputs": [{"id": f"note_{n}", "kind": "textarea"} for n in range(300)]},
    ]
    await auth_client.post(ROUTE, json={"title": "Cleanup", "actions": actions}, headers=ALICE)

    async def click(action_id: str, inputs: dict[str, Any]) -> int:
        response = await auth_client.post(
            f"{ROUTE}/actions/{action_id}/click", json={"inputs": inputs}, headers=ALICE
        )
        return response.status_code

    statuses = [
        await click("undo", {"reason": "x"}),
        await click("confirm", {"delete_b": True, "reason": "x"}),
        await click("confirm", {"delete_a": "yes", "reason": "x"}),
        await click("confirm", {"reason": "x" * 8_001}),
        await click("confirm", {"delete_a": True, "reason": "  "}),
        await click("confirm", {"delete_a": True}),
        await click("bulk", {f"note_{n}": "x" * 8_000 for n in range(300)}),
    ]

    assert nothing_active.status_code == 404
    assert statuses == [404, 400, 400, 400, 400, 400, 400]
    assert SqlAlchemyAgentQueueStore(db_uri).list(owner_user_id=ALICE_ID) == []
