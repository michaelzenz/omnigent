from omnigent.stores.model_settings_store.sqlalchemy_store import (
    SqlAlchemyModelSettingsStore,
)


def test_model_settings_store_reads_seed_and_updates(db_uri: str) -> None:
    store = SqlAlchemyModelSettingsStore(db_uri)

    settings = store.get()
    assert settings.harness_models["omniharness"] == [
        "databricks-gpt-5-6-luna",
        "databricks-glm-5-2",
        "databricks-kimi-k3",
    ]
    assert settings.policy_model is None
    assert settings.smart_routing_decision_model == "databricks-gpt-5-6-luna"
    assert settings.smart_routing_prompt == ""
    assert settings.smart_routing_cadence == "per_turn"
    assert settings.workload_classification_enabled is False
    assert settings.workload_custom_categories == ()

    updated = store.update(
        harness="omniharness",
        enabled_models=["databricks-kimi-k3", "databricks-kimi-k3"],
        policy_model="databricks-glm-5-2",
        update_policy_model=True,
        smart_routing_prompt="Choose the best model.",
        update_smart_routing_prompt=True,
        smart_routing_cadence="first_turn_only",
        update_smart_routing_cadence=True,
        workload_classification_enabled=True,
        update_workload_classification_enabled=True,
        workload_custom_categories=["research", "incident_response", "research"],
        update_workload_custom_categories=True,
        updated_by="admin",
    )

    assert updated.harness_models["omniharness"] == ["databricks-kimi-k3"]
    assert updated.policy_model == "databricks-glm-5-2"
    assert updated.smart_routing_decision_model == "databricks-gpt-5-6-luna"
    assert updated.smart_routing_prompt == "Choose the best model."
    assert updated.smart_routing_cadence == "first_turn_only"
    assert updated.workload_classification_enabled is True
    assert updated.workload_custom_categories == ("research", "incident_response")
    assert store.get() == updated

    cleared = store.update(
        smart_routing_decision_model=None,
        update_smart_routing_decision_model=True,
    )
    assert cleared.smart_routing_decision_model is None
    assert cleared.smart_routing_prompt == "Choose the best model."
    assert cleared.policy_model == "databricks-glm-5-2"
