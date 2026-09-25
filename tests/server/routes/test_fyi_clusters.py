"""Route tests for FYI clusters (``POST /v1/task-events/fyi-clusters``)."""

from __future__ import annotations

import uuid

import httpx

from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


async def test_create_fyi_cluster_classifies_events(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """POST fyi-clusters moves ambiguous events to classified_fyi."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("fyi-create-event")
    event_store.create_event(
        event_id,
        "build.finished",
        "Nightly build passed",
        state="awaiting_grouping",
    )

    resp = await client.post(
        "/v1/task-events/fyi-clusters",
        json={
            "event_ids": [event_id],
            "headline": "Nightly build green",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "agent.task.fyi_cluster"
    assert body["headline"] == "Nightly build green"
    assert event_store.get_event(event_id) is not None
    assert event_store.get_event(event_id).state == "classified_fyi"


async def test_extend_fyi_cluster_links_more_events(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """POST fyi-clusters with cluster_id attaches more events to an open card."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    first_event = _uid("fyi-extend-1")
    second_event = _uid("fyi-extend-2")
    for event_id in (first_event, second_event):
        event_store.create_event(
            event_id,
            "build.finished",
            "Nightly build passed",
            state="awaiting_grouping",
        )

    created = await client.post(
        "/v1/task-events/fyi-clusters",
        json={
            "event_ids": [first_event],
            "headline": "Nightly build green",
        },
    )
    cluster_id = created.json()["id"]

    extended = await client.post(
        "/v1/task-events/fyi-clusters",
        json={
            "event_ids": [second_event],
            "headline": "Nightly build green",
            "cluster_id": cluster_id,
        },
    )
    assert extended.status_code == 200
    assert extended.json()["id"] == cluster_id
    assert event_store.get_event(second_event) is not None
    assert event_store.get_event(second_event).state == "classified_fyi"


async def test_resolve_fyi_rejects_promote_resolution(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """promote_to_routing is retired; the resolve endpoint is dismiss-only."""
    import json

    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("fyi-promote-rejected-event")
    event_store.create_event(
        event_id,
        "build.finished",
        "Nightly build passed",
        state="awaiting_grouping",
    )

    created = await client.post(
        "/v1/task-events/fyi-clusters",
        json={"event_ids": [event_id], "headline": "Nightly build green"},
    )
    cluster_id = created.json()["id"]

    resolved = await client.post(
        f"/fyi-clusters/{cluster_id}/resolve",
        content=json.dumps({"resolution": "promote_to_routing"}),
        headers={"Content-Type": "application/json"},
    )
    assert resolved.status_code == 422
    # Nothing happened: the cluster stays open, the event stays classified_fyi.
    event = event_store.get_event(event_id)
    assert event is not None
    assert event.state == "classified_fyi"


async def test_resolve_fyi_dismisses_events(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """POST resolve with dismiss_fyi marks the cluster and its events dismissed."""
    event_store = SqlAlchemyTaskEventStore(db_uri)
    event_id = _uid("fyi-dismiss-event")
    event_store.create_event(
        event_id,
        "build.finished",
        "Nightly build passed",
        state="awaiting_grouping",
    )

    created = await client.post(
        "/v1/task-events/fyi-clusters",
        json={"event_ids": [event_id], "headline": "Nightly build green"},
    )
    cluster_id = created.json()["id"]

    resolved = await client.post(
        f"/fyi-clusters/{cluster_id}/resolve",
        json={"resolution": "dismiss_fyi"},
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["state"] == "dismissed"
    assert body["resolved_at"]
    event = event_store.get_event(event_id)
    assert event is not None
    assert event.state == "dismissed"
