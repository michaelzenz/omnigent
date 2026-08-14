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
