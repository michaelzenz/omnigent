from __future__ import annotations

import httpx


async def test_prompt_profile_crud(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/v1/prompt-profiles",
        json={
            "name": "Focused",
            "description": "Keep scope narrow",
            "instructions": "Work only on the requested task.",
        },
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]

    listed = await client.get("/v1/prompt-profiles?enabled_only=true")
    assert listed.status_code == 200
    assert [profile["id"] for profile in listed.json()] == [profile_id]

    patched = await client.patch(
        f"/v1/prompt-profiles/{profile_id}",
        json={"description": None, "enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["description"] is None
    assert patched.json()["enabled"] is False

    enabled = await client.get("/v1/prompt-profiles?enabled_only=true")
    assert enabled.json() == []

    deleted = await client.delete(f"/v1/prompt-profiles/{profile_id}")
    assert deleted.status_code == 204
    assert (await client.get("/v1/prompt-profiles")).json() == []


async def test_prompt_profile_rejects_duplicate_active_name(client: httpx.AsyncClient) -> None:
    body = {"name": "Same", "instructions": "Instructions"}
    assert (await client.post("/v1/prompt-profiles", json=body)).status_code == 201
    duplicate = await client.post("/v1/prompt-profiles", json=body)
    assert duplicate.status_code == 409
