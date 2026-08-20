"""SQLAlchemy-backed deployment model settings store."""

from __future__ import annotations

import json

from sqlalchemy import select

from omnigent.db.db_models import SqlModelSettings
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
)
from omnigent.stores.model_settings_store import ModelSettings, ModelSettingsStore


def _decode(row: SqlModelSettings) -> ModelSettings:
    raw = json.loads(row.harness_models)
    if not isinstance(raw, dict):
        raise ValueError("model_settings.harness_models must be an object")
    harness_models: dict[str, list[str]] = {}
    for harness, models in raw.items():
        if not isinstance(harness, str) or not isinstance(models, list):
            raise ValueError("model_settings.harness_models contains invalid data")
        if not all(isinstance(model, str) for model in models):
            raise ValueError("model_settings.harness_models contains invalid model ids")
        harness_models[harness] = list(models)
    workload_categories = json.loads(row.workload_custom_categories)
    if not isinstance(workload_categories, list) or not all(
        isinstance(category, str) for category in workload_categories
    ):
        raise ValueError("model_settings.workload_custom_categories must be a string list")
    return ModelSettings(
        harness_models=harness_models,
        policy_model=row.policy_model,
        smart_routing_decision_model=row.smart_routing_decision_model,
        smart_routing_prompt=row.smart_routing_prompt,
        smart_routing_cadence=row.smart_routing_cadence,
        omniharness_system_prompt=row.omniharness_system_prompt,
        workload_classification_enabled=row.workload_classification_enabled,
        workload_custom_categories=tuple(workload_categories),
    )


class SqlAlchemyModelSettingsStore(ModelSettingsStore):
    """Persist one global settings row for the deployment."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.model_settings_store",
        )

    def get(self) -> ModelSettings:
        with self._session("get") as session:
            row = session.get(SqlModelSettings, 1)
            if row is None:
                raise RuntimeError("global model settings row is missing")
            return _decode(row)

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
        omniharness_system_prompt: str | None = None,
        update_omniharness_system_prompt: bool = False,
        smart_routing_cadence: str | None = None,
        update_smart_routing_cadence: bool = False,
        workload_classification_enabled: bool | None = None,
        update_workload_classification_enabled: bool = False,
        workload_custom_categories: list[str] | None = None,
        update_workload_custom_categories: bool = False,
        updated_by: str | None = None,
    ) -> ModelSettings:
        if (harness is None) != (enabled_models is None):
            raise ValueError("harness and enabled_models must be updated together")
        with self._session("update") as session:
            row = session.execute(
                select(SqlModelSettings).where(SqlModelSettings.id == 1).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise RuntimeError("global model settings row is missing")
            if harness is not None and enabled_models is not None:
                settings = _decode(row)
                harness_models = dict(settings.harness_models)
                harness_models[harness] = list(dict.fromkeys(enabled_models))
                row.harness_models = json.dumps(harness_models, separators=(",", ":"))
            if update_policy_model:
                row.policy_model = policy_model
            if update_smart_routing_decision_model:
                row.smart_routing_decision_model = smart_routing_decision_model
            if update_smart_routing_prompt:
                row.smart_routing_prompt = smart_routing_prompt
            if update_omniharness_system_prompt:
                row.omniharness_system_prompt = omniharness_system_prompt or ""
            if update_smart_routing_cadence:
                if smart_routing_cadence not in {"per_turn", "first_turn_only"}:
                    raise ValueError("invalid smart routing cadence")
                row.smart_routing_cadence = smart_routing_cadence
            if update_workload_classification_enabled:
                if workload_classification_enabled is None:
                    raise ValueError("workload classification enabled must be a boolean")
                row.workload_classification_enabled = workload_classification_enabled
            if update_workload_custom_categories:
                if workload_custom_categories is None:
                    raise ValueError("workload custom categories must be a list")
                row.workload_custom_categories = json.dumps(
                    list(dict.fromkeys(workload_custom_categories)),
                    separators=(",", ":"),
                )
            row.updated_at = now_epoch()
            row.updated_by = updated_by
            return _decode(row)
