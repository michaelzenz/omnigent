from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from omnigent.model_catalog import ModelEntry
from omnigent.server.routes import model_settings
from omnigent.stores.model_settings_store import ModelSettings, ModelSettingsStore


class FakeModelSettingsStore(ModelSettingsStore):
    def __init__(self) -> None:
        super().__init__("memory")
        self.settings = ModelSettings(
            harness_models={
                "openai-agents": [
                    "databricks-gpt-5-6-luna",
                    "databricks-glm-5-2",
                    "databricks-kimi-k3",
                ]
            },
            policy_model="databricks-glm-5-2",
        )

    def get(self) -> ModelSettings:
        return self.settings

    def update(
        self,
        *,
        harness: str | None = None,
        enabled_models: list[str] | None = None,
        policy_model: str | None = None,
        update_policy_model: bool = False,
        updated_by: str | None = None,
    ) -> ModelSettings:
        del updated_by
        harness_models = dict(self.settings.harness_models)
        if harness is not None and enabled_models is not None:
            harness_models[harness] = enabled_models
        self.settings = ModelSettings(
            harness_models=harness_models,
            policy_model=policy_model if update_policy_model else self.settings.policy_model,
        )
        return self.settings


@pytest.mark.asyncio
async def test_model_settings_routes_discover_and_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeModelSettingsStore()
    caps = SimpleNamespace(
        llm=model_settings.LLMConfig(model="databricks-glm-5-2", profile="workspace")
    )
    monkeypatch.setattr(model_settings, "get_caps", lambda: caps)
    monkeypatch.setattr(
        model_settings,
        "_serving_models",
        lambda _profile: [
            ModelEntry(id="databricks-glm-5-2", family="other"),
            ModelEntry(id="databricks-gpt-5-4", family="openai"),
        ],
    )

    app = FastAPI()
    app.include_router(
        model_settings.create_model_settings_router(
            store,
            server_config={"auth": {"type": "databricks", "profile": "workspace"}},
        ),
        prefix="/v1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        discovered = await client.get("/v1/admin/model-settings")
        assert discovered.status_code == 200
        assert [model["id"] for model in discovered.json()["models"]] == [
            "databricks-glm-5-2",
            "databricks-gpt-5-4",
        ]

        updated = await client.patch(
            "/v1/admin/model-settings",
            json={
                "omnigent_models": ["databricks-gpt-5-4"],
                "policy_model": "databricks-gpt-5-4",
            },
        )
        assert updated.status_code == 200

        options = await client.get("/v1/model-options")
        assert [model["id"] for model in options.json()["data"]] == ["databricks-gpt-5-4"]

    assert store.get().harness_models["openai-agents"] == ["databricks-gpt-5-4"]
    assert store.get().policy_model == "databricks-gpt-5-4"
    assert caps.llm.model == "databricks-gpt-5-4"
