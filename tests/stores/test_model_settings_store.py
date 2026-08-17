from omnigent.stores.model_settings_store.sqlalchemy_store import (
    SqlAlchemyModelSettingsStore,
)


def test_model_settings_store_reads_seed_and_updates(db_uri: str) -> None:
    store = SqlAlchemyModelSettingsStore(db_uri)

    settings = store.get()
    assert settings.harness_models["openai-agents"] == [
        "databricks-gpt-5-6-luna",
        "databricks-glm-5-2",
        "databricks-kimi-k3",
    ]
    assert settings.policy_model is None

    updated = store.update(
        harness="openai-agents",
        enabled_models=["databricks-kimi-k3", "databricks-kimi-k3"],
        policy_model="databricks-glm-5-2",
        update_policy_model=True,
        updated_by="admin",
    )

    assert updated.harness_models["openai-agents"] == ["databricks-kimi-k3"]
    assert updated.policy_model == "databricks-glm-5-2"
    assert store.get() == updated
