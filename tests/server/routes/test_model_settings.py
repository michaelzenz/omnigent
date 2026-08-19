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
            smart_routing_decision_model="databricks-gpt-5-6-luna",
            smart_routing_prompt="",
            smart_routing_cadence="per_turn",
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
        smart_routing_decision_model: str | None = None,
        update_smart_routing_decision_model: bool = False,
        smart_routing_prompt: str | None = None,
        update_smart_routing_prompt: bool = False,
        smart_routing_cadence: str | None = None,
        update_smart_routing_cadence: bool = False,
        updated_by: str | None = None,
    ) -> ModelSettings:
        del updated_by
        harness_models = dict(self.settings.harness_models)
        if harness is not None and enabled_models is not None:
            harness_models[harness] = enabled_models
        self.settings = ModelSettings(
            harness_models=harness_models,
            policy_model=policy_model if update_policy_model else self.settings.policy_model,
            smart_routing_decision_model=(
                smart_routing_decision_model
                if update_smart_routing_decision_model
                else self.settings.smart_routing_decision_model
            ),
            smart_routing_prompt=(
                smart_routing_prompt
                if update_smart_routing_prompt
                else self.settings.smart_routing_prompt
            ),
            smart_routing_cadence=(
                smart_routing_cadence
                if update_smart_routing_cadence and smart_routing_cadence is not None
                else self.settings.smart_routing_cadence
            ),
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
        assert discovered.json()["smart_routing_decision_model"] == "databricks-gpt-5-6-luna"
        assert discovered.json()["smart_routing_prompt"] == ""
        assert discovered.json()["smart_routing_cadence"] == "per_turn"

        updated = await client.patch(
            "/v1/admin/model-settings",
            json={
                "omnigent_models": ["databricks-gpt-5-4"],
                "policy_model": "databricks-gpt-5-4",
                "smart_routing_decision_model": "databricks-glm-5-2",
                "smart_routing_prompt": "Choose the best configured model.",
                "smart_routing_cadence": "first_turn_only",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["smart_routing_decision_model"] == "databricks-glm-5-2"
        assert updated.json()["smart_routing_prompt"] == "Choose the best configured model."
        assert updated.json()["smart_routing_cadence"] == "first_turn_only"

        cleared = await client.patch(
            "/v1/admin/model-settings",
            json={"smart_routing_decision_model": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["smart_routing_decision_model"] is None
        assert cleared.json()["smart_routing_prompt"] == "Choose the best configured model."
        assert cleared.json()["smart_routing_cadence"] == "first_turn_only"

        options = await client.get("/v1/model-options")
        assert [model["id"] for model in options.json()["data"]] == ["databricks-gpt-5-4"]

    assert store.get().harness_models["openai-agents"] == ["databricks-gpt-5-4"]
    assert store.get().policy_model == "databricks-gpt-5-4"
    assert store.get().smart_routing_decision_model is None
    assert store.get().smart_routing_prompt == "Choose the best configured model."
    assert store.get().smart_routing_cadence == "first_turn_only"
    assert caps.llm.model == "databricks-gpt-5-4"


@pytest.mark.asyncio
async def test_model_settings_route_validates_smart_routing_fields() -> None:
    app = FastAPI()
    app.include_router(
        model_settings.create_model_settings_router(FakeModelSettingsStore()),
        prefix="/v1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        invalid_cadence = await client.patch(
            "/v1/admin/model-settings",
            json={"smart_routing_cadence": "sometimes"},
        )
        long_model = await client.patch(
            "/v1/admin/model-settings",
            json={"smart_routing_decision_model": "m" * 301},
        )
        long_prompt = await client.patch(
            "/v1/admin/model-settings",
            json={"smart_routing_prompt": "p" * 20_001},
        )

    assert invalid_cadence.status_code == 422
    assert long_model.status_code == 422
    assert long_prompt.status_code == 422
