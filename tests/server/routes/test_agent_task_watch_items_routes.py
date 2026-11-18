"""Tests for the routes of what GlobalHub watchers track (``.../watchers/.../items``)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from omnigent.entities import SESSION_EVENT_SOURCE
from omnigent.runner.identity import (
    ORCHESTRATION_SOURCE_HEADER,
    RUNNER_TUNNEL_TOKEN_HEADER,
    token_bound_runner_id,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.subscription_store.sqlalchemy_store import SqlAlchemySubscriptionStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.user_settings_store import GlobalHubPlacement, UserSettingsStore

ALICE_ID = "alice@example.com"
ALICE = {"X-Forwarded-Email": ALICE_ID}
BOB = {"X-Forwarded-Email": "bob@example.com"}
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
WATCHERS = "/v1/agent-tasks/watchers"
OWN_ITEMS = f"{WATCHERS}/self/items"
PULL_REQUEST = "github.pull_request"
PR = "https://github.com/example-org/repo/pull/12"
OTHER_PR = "https://github.com/example-org/repo/pull/13"
THIRD_PR = "https://github.com/example-org/repo/pull/14"


async def _watcher_session(client: httpx.AsyncClient, db_uri: str) -> tuple[str, dict[str, str]]:
    """Create a watcher with its session; return its id and its runner's headers."""
    SqlAlchemyAgentStore(db_uri).create(
        uuid.uuid4().hex, "onih-watcher", "bundles/onih-watcher.tar.gz"
    )
    UserSettingsStore(db_uri).save_globalhub_placement(
        GlobalHubPlacement(host_id=HOST_ID, workspace="/Users/alice/globalhub"),
        user_id=ALICE_ID,
    )
    created = await client.post(WATCHERS, json={"name": "PR watcher"}, headers=ALICE)
    watcher_id = created.json()["id"]
    session = await client.post(f"{WATCHERS}/{watcher_id}/session", headers=ALICE)
    session_id = session.json()["session_id"]
    runner_token = "pr-watcher-runner"
    SqlAlchemyConversationStore(db_uri).set_runner_id(
        session_id, token_bound_runner_id(runner_token)
    )
    return watcher_id, {
        **ALICE,
        ORCHESTRATION_SOURCE_HEADER: session_id,
        RUNNER_TUNNEL_TOKEN_HEADER: runner_token,
    }


def _follow(db_uri: str, session_id: str, source_key: str) -> None:
    SqlAlchemySubscriptionStore(db_uri).subscribe(
        owner_user_id=ALICE_ID,
        session_id=session_id,
        source_type=PULL_REQUEST,
        source_key=source_key,
    )


def _item(source_key: str = PR, **fields: Any) -> dict[str, Any]:
    return {
        "source": PULL_REQUEST,
        "source_key": source_key,
        "offsets": {"state": "open"},
        **fields,
    }


async def test_only_the_watchers_own_session_uses_its_tracker(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _, own_runner = await _watcher_session(auth_client, db_uri)
    body = {"items": [_item()]}
    keys = {"items": [{"source": PULL_REQUEST, "source_key": PR}]}

    refused = [
        await auth_client.get(OWN_ITEMS, headers=ALICE),
        await auth_client.post(f"{OWN_ITEMS}/observe", json=body, headers=ALICE),
        await auth_client.post(
            f"{OWN_ITEMS}/observe",
            json=body,
            headers={**own_runner, RUNNER_TUNNEL_TOKEN_HEADER: "another-runner"},
        ),
        # Bob's request through Alice's watcher runner.
        await auth_client.post(f"{OWN_ITEMS}/done", json=keys, headers={**own_runner, **BOB}),
    ]
    listed = await auth_client.get(OWN_ITEMS, headers=own_runner)

    assert [response.status_code for response in refused] == [403, 403, 403, 403]
    assert listed.json() == {
        "object": "list",
        "data": [],
        "first_id": None,
        "last_id": None,
        "has_more": False,
        "total": 0,
    }


async def test_observe_reports_changes_to_the_sessions_that_follow_an_item_else_the_broker(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    watcher_id, own_runner = await _watcher_session(auth_client, db_uri)
    watcher_session = own_runner[ORCHESTRATION_SOURCE_HEADER]
    follower = uuid.uuid4().hex
    _follow(db_uri, follower, OTHER_PR)
    _follow(db_uri, watcher_session, THIRD_PR)

    first = await auth_client.post(
        f"{OWN_ITEMS}/observe",
        json={"items": [_item(title="repo#12"), _item(f"{OTHER_PR}/"), _item(THIRD_PR)]},
        headers=own_runner,
    )
    second = await auth_client.post(
        f"{OWN_ITEMS}/observe",
        json={
            "items": [
                _item(offsets={"state": "merged"}, summary="Merged"),
                _item(OTHER_PR, offsets={"state": "closed"}),
                _item(THIRD_PR, offsets={"state": "closed"}),
            ]
        },
        headers=own_runner,
    )

    assert [
        (row["source"], row["source_key"], row["outcome"], row["event_ids"])
        for row in first.json()["data"]
    ] == [
        (PULL_REQUEST, PR, "new", []),
        (PULL_REQUEST, OTHER_PR, "new", []),
        (PULL_REQUEST, THIRD_PR, "new", []),
    ]
    merged, closed, own = second.json()["data"]
    assert (merged["outcome"], merged["changes"]) == (
        "changed",
        [{"offset": "state", "before": "open", "after": "merged"}],
    )
    assert (own["outcome"], own["event_ids"]) == ("changed", [])
    events = SqlAlchemyTaskEventStore(db_uri)
    (broker_event_id,) = merged["event_ids"]
    (follower_event_id,) = closed["event_ids"]
    to_broker = events.get(broker_event_id, owner_user_id=ALICE_ID)
    to_follower = events.get(follower_event_id, owner_user_id=ALICE_ID)
    assert to_broker is not None and to_follower is not None
    assert (to_broker.state, to_broker.title, to_broker.payload["watcher"]["id"]) == (
        "unrouted",
        "Merged",
        watcher_id,
    )
    assert (to_follower.state, to_follower.target_session_id, to_follower.source) == (
        "routed",
        follower,
        PULL_REQUEST,
    )
    assert to_broker.source_session_id == watcher_session


async def test_observe_refuses_a_bad_call_and_saves_nothing(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _, own_runner = await _watcher_session(auth_client, db_uri)
    path = f"{OWN_ITEMS}/observe"

    responses = [
        # Subscriptions route a watcher's changes, so an item names no task.
        await auth_client.post(
            path, json={"items": [_item(task_id=uuid.uuid4().hex)]}, headers=own_runner
        ),
        await auth_client.post(path, json={"items": [_item(), _item()]}, headers=own_runner),
        await auth_client.post(
            path,
            json={"items": [_item(offsets={f"o{n}": "x" for n in range(11)})]},
            headers=own_runner,
        ),
        await auth_client.post(path, json={"items": []}, headers=own_runner),
        await auth_client.post(
            path,
            json={"items": [_item(), _item(source=SESSION_EVENT_SOURCE)]},
            headers=own_runner,
        ),
        await auth_client.post(path, json={"items": [_item(source="github")]}, headers=own_runner),
        await auth_client.post(
            path, json={"items": [_item(), _item(f"{PR}/")]}, headers=own_runner
        ),
    ]

    assert [response.status_code for response in responses] == [422, 422, 422, 422, 400, 400, 400]
    assert (await auth_client.get(OWN_ITEMS, headers=own_runner)).json()["total"] == 0


async def test_a_watcher_pages_through_any_number_of_items(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    watcher_id, own_runner = await _watcher_session(auth_client, db_uri)
    keys = [f"https://example.com/pr/{n}" for n in range(250)]
    for start in (0, 100, 200):
        batch = {"items": [_item(key) for key in keys[start : start + 100]]}
        observed = await auth_client.post(f"{OWN_ITEMS}/observe", json=batch, headers=own_runner)
        assert observed.status_code == 200, observed.text

    seen: list[str] = []
    after: str | None = None
    while True:
        query = {"limit": 120, **({"after": after} if after else {})}
        page = (await auth_client.get(OWN_ITEMS, params=query, headers=own_runner)).json()
        seen.extend(row["source_key"] for row in page["data"])
        if not page["has_more"]:
            break
        after = page["last_id"]
    card = await auth_client.get(
        f"{WATCHERS}/{watcher_id}/items", params={"limit": 1}, headers=ALICE
    )
    stale = await auth_client.get(OWN_ITEMS, params={"after": "not-an-item"}, headers=own_runner)
    too_big = await auth_client.get(OWN_ITEMS, params={"limit": 501}, headers=own_runner)

    # Each call observes its items in one second, so pages compare them as a set.
    assert sorted(seen) == sorted(keys)
    assert (card.json()["total"], card.json()["has_more"], len(card.json()["data"])) == (
        250,
        True,
        1,
    )
    assert (stale.status_code, too_big.status_code) == (400, 422)


async def test_done_items_leave_the_active_list_and_the_card_shows_them_all(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    watcher_id, own_runner = await _watcher_session(auth_client, db_uri)
    await auth_client.post(
        f"{OWN_ITEMS}/observe", json={"items": [_item(), _item(OTHER_PR)]}, headers=own_runner
    )
    gone = {"source": PULL_REQUEST, "source_key": "https://example.com/x"}

    with_offsets = await auth_client.post(
        f"{OWN_ITEMS}/done", json={"items": [_item()]}, headers=own_runner
    )
    twice = await auth_client.post(
        f"{OWN_ITEMS}/done",
        json={"items": [{"source": PULL_REQUEST, "source_key": PR}] * 2},
        headers=own_runner,
    )
    done = await auth_client.post(
        f"{OWN_ITEMS}/done",
        json={"items": [{"source": PULL_REQUEST, "source_key": PR}, gone]},
        headers=own_runner,
    )
    active = (await auth_client.get(OWN_ITEMS, headers=own_runner)).json()
    everything = await auth_client.get(
        f"{WATCHERS}/{watcher_id}/items", params={"status": "all"}, headers=ALICE
    )
    for_bob = await auth_client.get(f"{WATCHERS}/{watcher_id}/items", headers=BOB)

    assert (with_offsets.status_code, twice.status_code) == (422, 400)
    assert done.json() == {"done": 1, "missing": [gone]}
    assert ([row["source_key"] for row in active["data"]], active["total"]) == ([OTHER_PR], 1)
    assert everything.json()["total"] == 2
    assert for_bob.status_code == 404


async def test_a_done_item_tells_the_sessions_that_follow_it_and_reopens_when_it_changes(
    auth_client: httpx.AsyncClient, db_uri: str
) -> None:
    _, own_runner = await _watcher_session(auth_client, db_uri)
    follower = uuid.uuid4().hex
    _follow(db_uri, follower, PR)
    observe = f"{OWN_ITEMS}/observe"
    await auth_client.post(observe, json={"items": [_item(title="repo#12")]}, headers=own_runner)

    done = await auth_client.post(
        f"{OWN_ITEMS}/done",
        json={"items": [{"source": PULL_REQUEST, "source_key": f"{PR}/", "summary": " Merged "}]},
        headers=own_runner,
    )
    done_again = await auth_client.post(
        f"{OWN_ITEMS}/done",
        json={"items": [{"source": PULL_REQUEST, "source_key": PR, "summary": "Merged"}]},
        headers=own_runner,
    )
    unchanged = await auth_client.post(observe, json={"items": [_item()]}, headers=own_runner)
    active_while_done = (await auth_client.get(OWN_ITEMS, headers=own_runner)).json()["total"]
    reopened = await auth_client.post(
        observe,
        json={"items": [_item(offsets={"checks": "failing"}, summary="CI failed: unit-tests")]},
        headers=own_runner,
    )
    active = (await auth_client.get(OWN_ITEMS, headers=own_runner)).json()

    events = SqlAlchemyTaskEventStore(db_uri)
    [done_event] = events.list(owner_user_id=ALICE_ID, event_type="watch.done")
    [reopen_event] = events.list(owner_user_id=ALICE_ID, event_type="watch.reopened")
    assert done.json() == done_again.json() == {"done": 1, "missing": []}
    assert (done_event.title, done_event.target_session_id, done_event.state) == (
        "Done: repo#12: Merged",
        follower,
        "routed",
    )
    assert (unchanged.json()["data"][0]["outcome"], active_while_done) == ("unchanged", 0)
    [result] = reopened.json()["data"]
    assert (result["outcome"], result["event_ids"]) == ("reopened", [reopen_event.id])
    assert (reopen_event.title, reopen_event.target_session_id) == (
        "CI failed: unit-tests",
        follower,
    )
    assert [row["source_key"] for row in active["data"]] == [PR]
