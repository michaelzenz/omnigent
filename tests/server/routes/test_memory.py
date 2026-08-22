from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server.routes.memory import create_memory_router
from omnigent.stores.memory_store.sqlalchemy_store import SqlAlchemyMemoryStore


def test_memory_api_crud_and_usage(db_uri: str) -> None:
    app = FastAPI()
    app.include_router(
        create_memory_router(
            memory_store=SqlAlchemyMemoryStore(db_uri),
            auth_provider=None,
            max_tokens=2,
        ),
        prefix="/v1",
    )
    client = TestClient(app)

    initial = client.get("/v1/memory").json()
    assert len(initial["categories"]) == 4
    assert initial["max_tokens"] == 2
    assert initial["provider"] == "omniharness"

    settings = client.patch(
        "/v1/memory/settings",
        json={"max_tokens": 3, "provider": "agents"},
    ).json()
    assert settings["max_tokens"] == 3
    assert settings["provider"] == "agents"

    created = client.post(
        "/v1/memory/categories",
        json={"name": "Custom", "content": "one two three four"},
    ).json()
    custom = next(category for category in created["categories"] if category["name"] == "Custom")
    assert created["used_tokens"] >= custom["token_count"] > 2
    assert created["usage_percent"] > 100
    assert created["over_limit"] is True

    updated = client.patch(
        f"/v1/memory/categories/{custom['id']}",
        json={"content": "short"},
    ).json()
    assert (
        next(category for category in updated["categories"] if category["id"] == custom["id"])[
            "content"
        ]
        == "short"
    )

    ordered_ids = [category["id"] for category in reversed(updated["categories"])]
    reordered = client.put("/v1/memory/order", json={"ordered_ids": ordered_ids}).json()
    assert [category["id"] for category in reordered["categories"]] == ordered_ids
