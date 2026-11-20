"""Tests for the GlobalHub system notice routes (``/v1/agent-tasks/system-notice``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.agent_tasks.roles import CUSTODIAN_ROLE_KEY
from omnigent.agent_tasks.session_labels import (
    CUSTODIAN_ROLE_VALUE,
    MANAGER_ROLE_VALUE,
    ROLE_LABEL,
)
from omnigent.runner.identity import (
    ORCHESTRATION_SOURCE_HEADER,
    RUNNER_TUNNEL_TOKEN_HEADER,
    token_bound_runner_id,
)
from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

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


async def test_only_the_custodians_own_runner_publishes_and_names_it_as_the_publisher(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    conversations = SqlAlchemyConversationStore(db_uri)
    session = conversations.create_conversation(title="Task custodian")
    conversations.set_labels(session.id, {ROLE_LABEL: CUSTODIAN_ROLE_VALUE})
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
    assert unconfirmed.status_code == 403


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


DRAFT = f"{ROUTE}/draft"


def _task(db_uri: str, title: str, state: str = "active", manager_id: str | None = None) -> str:
    task_id = uuid.uuid4().hex
    SqlAlchemyTaskStore(db_uri).create(
        task_id, owner_user_id=ALICE_ID, title=title, state=state, manager_id=manager_id
    )
    return task_id


def _session(db_uri: str, title: str, owner: str = ALICE_ID, role: str | None = None) -> str:
    conversations = SqlAlchemyConversationStore(db_uri)
    session = conversations.create_conversation(title=title)
    if role is not None:
        conversations.set_labels(session.id, {ROLE_LABEL: role})
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user(owner)
    permissions.grant(owner, session.id, LEVEL_OWNER)
    return session.id


async def _stage(client: httpx.AsyncClient, change: dict[str, Any]) -> httpx.Response:
    return await client.post(f"{DRAFT}/changes", json={"change": change}, headers=ALICE)


async def test_staged_changes_stay_private_until_one_commit_publishes_them(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    kept = _task(db_uri, "Fix S3 retries")
    duplicate = _task(db_uri, "Retry fix for S3")
    mixed = _task(db_uri, "Alerts and quota", manager_id=uuid.uuid4().hex)
    worker = _session(db_uri, "Quota request")
    SqlAlchemyWorkerStore(db_uri).create(
        uuid.uuid4().hex, owner_user_id=ALICE_ID, task_id=mixed, session_id=worker
    )
    split = {
        "kind": "split_task",
        "task": mixed,
        "new_title": "Quota request",
        "sessions": [worker],
        "summary": "Two unrelated pieces of work",
    }
    merge = {
        "kind": "merge_tasks",
        "keep": kept,
        "merge": [duplicate],
        "summary": "Both fix PR 12",
    }

    await _stage(auth_client, split)
    staged = await _stage(auth_client, {**merge, "summary": "First wording"})
    restaged = (await _stage(auth_client, merge)).json()
    before_commit = (await auth_client.get(ROUTE, headers=ALICE)).json()["notice"]
    committed = await auth_client.post(f"{DRAFT}/commit", json={}, headers=ALICE)
    notice = committed.json()["notice"]
    after_commit = (await auth_client.get(DRAFT, headers=ALICE)).json()

    assert staged.status_code == 200, staged.text
    merge_id, split_id = f"merge_tasks__{kept}", f"split_task__{mixed}"
    assert [change["id"] for change in restaged["changes"]] == [split_id, merge_id]
    assert (restaged["changes"][1]["summary"], restaged["problems"]) == ("Both fix PR 12", [])
    assert before_commit is None
    assert committed.status_code == 200, committed.text
    assert notice["title"] == "2 task changes to review"
    [action] = notice["actions"]
    assert action["id"] == "apply_changes"
    assert [(item["id"], item["group"], item["default"]) for item in action["inputs"]] == [
        (merge_id, "Merge tasks", False),
        (split_id, "Split tasks", False),
    ]
    assert "Keep _Fix S3 retries_; merge in and delete _Retry fix for S3_." in notice["body"]
    assert f"[Quota request](/c/{worker})" in notice["body"]
    assert [change["id"] for change in notice["changes"]] == [merge_id, split_id]
    assert after_commit == {
        "object": "agent.task.system_notice.draft",
        "changes": [],
        "problems": [],
    }
    assert (await auth_client.get(ROUTE, headers=BOB)).json()["notice"] is None


async def test_the_final_check_keeps_a_draft_with_problems_from_going_out(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    live = _task(db_uri, "Fix S3 retries")
    archived = _task(db_uri, "Old work", state="archived")
    kept = _session(db_uri, "Backfill alert")
    merged = _session(db_uri, "Backfill alert again")
    bobs = _session(db_uri, "Bob's session", owner="bob@example.com")
    manager = _session(db_uri, "Reviews manager", role=MANAGER_ROLE_VALUE)

    for change in [
        {"kind": "merge_tasks", "keep": live, "merge": [archived], "summary": "Same work"},
        {"kind": "merge_sessions", "keep": kept, "merge": [merged], "summary": "Same alert"},
        {"kind": "delete_session", "session": merged, "summary": "Idle"},
        {"kind": "delete_session", "session": bobs, "summary": "Idle"},
        {"kind": "delete_session", "session": manager, "summary": "Idle"},
    ]:
        assert (await _stage(auth_client, change)).status_code == 200
    problems = (await auth_client.get(DRAFT, headers=ALICE)).json()["problems"]
    refused = await auth_client.post(f"{DRAFT}/commit", json={}, headers=ALICE)
    unknown = await auth_client.delete(f"{DRAFT}/changes/delete_session__nothing", headers=ALICE)
    discarded = await auth_client.delete(DRAFT, headers=ALICE)
    empty_commit = await auth_client.post(f"{DRAFT}/commit", json={}, headers=ALICE)

    assert problems == [
        "A notice holds task changes or session changes, not both. Commit the task changes "
        "first, and stage the session changes after you made the task changes.",
        f"merge_tasks__{live}: task {archived} is not one of your live tasks.",
        f"delete_session__{bobs}: session {bobs} is not one of your sessions.",
        f"delete_session__{manager}: session {manager} is a GlobalHub role session.",
        f"merge_sessions__{kept}: it removes {merged}, which delete_session__{merged} also names.",
        f"delete_session__{merged}: it removes {merged}, which merge_sessions__{kept} also names.",
    ]
    assert refused.status_code == 400
    assert (await auth_client.get(ROUTE, headers=ALICE)).json()["notice"] is None
    assert unknown.status_code == 404
    assert discarded.json()["changes"] == []
    assert empty_commit.status_code == 400


async def test_a_staged_change_names_tasks_and_sessions_by_their_canonical_ids(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    kept = _task(db_uri, "Fix S3 retries")
    another_spelling = str(uuid.UUID(kept)).upper()

    malformed = await _stage(
        auth_client,
        {"kind": "merge_tasks", "keep": kept, "merge": ["Fix S3 retries"], "summary": "Same"},
    )
    staged = await _stage(
        auth_client,
        {"kind": "merge_tasks", "keep": kept, "merge": [another_spelling], "summary": "Same"},
    )

    assert malformed.status_code == 422
    assert staged.json()["changes"][0]["merge"] == [kept]
    assert staged.json()["problems"] == [f"merge_tasks__{kept}: it keeps and merges {kept}."]


async def test_only_the_user_clicks_and_only_the_custodian_or_user_stages(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _place_globalhub_with_a_custodian(db_uri)
    idle = _session(db_uri, "Idle chat")
    conversations = SqlAlchemyConversationStore(db_uri)

    def runner_headers(role: str) -> dict[str, str]:
        session_id = _session(db_uri, f"{role} session", role=role)
        conversations.set_runner_id(session_id, token_bound_runner_id(f"{role}-runner"))
        return {
            **ALICE,
            ORCHESTRATION_SOURCE_HEADER: session_id,
            RUNNER_TUNNEL_TOKEN_HEADER: f"{role}-runner",
        }

    change = {"change": {"kind": "delete_session", "session": idle, "summary": "Idle"}}
    manager = runner_headers(MANAGER_ROLE_VALUE)
    by_manager = await auth_client.post(f"{DRAFT}/changes", json=change, headers=manager)
    published_by_manager = await auth_client.post(ROUTE, json=CLEANUP, headers=manager)
    dismissed_by_manager = await auth_client.post(f"{ROUTE}/dismiss", headers=manager)
    custodian = runner_headers(CUSTODIAN_ROLE_VALUE)
    by_custodian = await auth_client.post(f"{DRAFT}/changes", json=change, headers=custodian)
    committed = await auth_client.post(f"{DRAFT}/commit", headers=custodian)
    agent_click = await auth_client.post(
        f"{ROUTE}/actions/apply_changes/click",
        json={"inputs": {f"delete_session__{idle}": True}},
        headers=custodian,
    )

    assert [response.status_code for response in (by_manager, published_by_manager)] == [403, 403]
    assert dismissed_by_manager.status_code == 403
    assert by_custodian.status_code == 200, by_custodian.text
    assert committed.json()["notice"]["publisher_label"] == "task_custodian session"
    assert agent_click.status_code == 403
    assert SqlAlchemyAgentQueueStore(db_uri).list(owner_user_id=ALICE_ID) == []


async def test_a_draft_holds_at_most_one_notice_of_changes(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    sessions = [_session(db_uri, f"Idle session {n}") for n in range(11)]

    statuses = [
        (
            await _stage(
                auth_client, {"kind": "delete_session", "session": session, "summary": "Idle"}
            )
        ).status_code
        for session in sessions
    ]

    assert statuses == [200] * 10 + [400]
    assert len((await auth_client.get(DRAFT, headers=ALICE)).json()["changes"]) == 10


async def test_a_click_on_a_committed_notice_carries_the_approved_changes(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _place_globalhub_with_a_custodian(db_uri)
    approved, declined = _session(db_uri, "Finished spike"), _session(db_uri, "Idle chat")
    for session in (approved, declined):
        change = {"kind": "delete_session", "session": session, "summary": "Idle"}
        await _stage(auth_client, {**change, "details": "Its pull request merged."})
    await auth_client.post(f"{DRAFT}/commit", json={"title": "Two idle sessions"}, headers=ALICE)

    clicked = await auth_client.post(
        f"{ROUTE}/actions/apply_changes/click",
        json={"inputs": {f"delete_session__{approved}": True}},
        headers=ALICE,
    )

    assert clicked.status_code == 200, clicked.text
    [item] = SqlAlchemyAgentQueueStore(db_uri).list(owner_user_id=ALICE_ID)
    assert item.payload["title"] == "Two idle sessions"
    assert item.payload["changes"] == [
        {
            "id": f"delete_session__{approved}",
            "kind": "delete_session",
            "session": approved,
            "summary": "Idle",
        }
    ]
