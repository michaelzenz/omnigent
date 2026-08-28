"""Tests for task event subscription CRUD (``/v1/agent-tasks/{id}/event-subscriptions``)."""

from __future__ import annotations

import httpx


async def _create_task(client: httpx.AsyncClient, title: str = "Watch PRs") -> str:
    # Pending tasks skip manager bootstrap, so no live host is required.
    created = await client.post(
        "/v1/agent-tasks",
        json={"title": title, "goal": f"{title} goal", "state": "pending"},
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


async def test_event_subscription_crud(client: httpx.AsyncClient) -> None:
    task_id = await _create_task(client)

    created = await client.post(
        f"/v1/agent-tasks/{task_id}/event-subscriptions",
        json={"source": "poll_plugin:github_pr", "source_key": "org/repo#456"},
    )
    assert created.status_code == 201, created.text
    subscription = created.json()
    assert subscription["task_id"] == task_id
    assert subscription["source"] == "poll_plugin:github_pr"
    assert subscription["source_key"] == "org/repo#456"

    listed = await client.get(f"/v1/agent-tasks/{task_id}/event-subscriptions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["data"]] == [subscription["id"]]

    deleted = await client.delete(
        f"/v1/agent-tasks/{task_id}/event-subscriptions/{subscription['id']}"
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    listed = await client.get(f"/v1/agent-tasks/{task_id}/event-subscriptions")
    assert listed.json()["data"] == []


async def test_event_subscription_create_is_idempotent(client: httpx.AsyncClient) -> None:
    task_id = await _create_task(client)
    body = {"source": "poll_plugin:github_pr", "source_key": "org/repo#456"}
    first = await client.post(f"/v1/agent-tasks/{task_id}/event-subscriptions", json=body)
    second = await client.post(f"/v1/agent-tasks/{task_id}/event-subscriptions", json=body)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listed = await client.get(f"/v1/agent-tasks/{task_id}/event-subscriptions")
    assert len(listed.json()["data"]) == 1


async def test_event_subscription_delete_scoped_to_task(client: httpx.AsyncClient) -> None:
    task_id = await _create_task(client, "Owner task")
    other_task_id = await _create_task(client, "Other task")
    created = await client.post(
        f"/v1/agent-tasks/{task_id}/event-subscriptions",
        json={"source": "ci", "source_key": "build-1"},
    )
    subscription_id = created.json()["id"]

    wrong_task = await client.delete(
        f"/v1/agent-tasks/{other_task_id}/event-subscriptions/{subscription_id}"
    )
    assert wrong_task.status_code == 404

    missing = await client.delete(
        f"/v1/agent-tasks/{task_id}/event-subscriptions/{subscription_id}ffff"
    )
    assert missing.status_code == 404
