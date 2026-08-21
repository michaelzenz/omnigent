"""Admin-managed Databricks model settings."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.execution_targets import OMNIHARNESS_AGENT_NAME
from omnigent.model_catalog import ModelEntry, model_family_token
from omnigent.omniharness_model_catalog import (
    get_omniharness_model_metadata,
    refresh_omniharness_model_catalog,
)
from omnigent.omniharness_turn_selection import DEFAULT_WORKLOAD_CATEGORIES
from omnigent.runtime import get_caps
from omnigent.runtime.credentials.databricks import resolve_databricks_workspace
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_user
from omnigent.spec.types import LLMConfig
from omnigent.stores.model_settings_store import ModelSettingsStore
from omnigent.stores.permission_store import PermissionStore

_WORKLOAD_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class UpdateModelSettingsRequest(BaseModel):
    """Fields an admin may update independently."""

    omniharness_models: list[str] | None = Field(default=None, max_length=500)
    policy_model: str | None = Field(default=None, max_length=300)
    smart_routing_decision_model: str | None = Field(default=None, max_length=300)
    smart_routing_prompt: str | None = Field(default=None, max_length=20_000)
    smart_routing_cadence: Literal["per_turn", "first_turn_only"] = "per_turn"
    turn_selection_user_message_count: int = Field(default=3, ge=1)
    workload_classification_enabled: bool = False
    workload_custom_categories: list[str] | None = Field(default=None, max_length=20)

    @field_validator("workload_custom_categories")
    @classmethod
    def validate_workload_categories(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) != len(set(value)) or any(
            not _WORKLOAD_CATEGORY_RE.fullmatch(category)
            or category in DEFAULT_WORKLOAD_CATEGORIES
            for category in value
        ):
            raise ValueError(
                "custom categories must be unique lowercase identifiers up to "
                "32 characters and cannot duplicate built-in categories"
            )
        return value


class AdminModelSettingsResponse(BaseModel):
    """Admin model settings and available Databricks models."""

    object: Literal["model_settings"]
    databricks_connected: bool
    profile: str | None
    models: list[dict[str, Any]]
    omniharness_models: list[str]
    policy_model: str | None
    smart_routing_decision_model: str | None
    smart_routing_prompt: str | None
    smart_routing_cadence: Literal["per_turn", "first_turn_only"]
    turn_selection_user_message_count: int
    workload_classification_enabled: bool
    workload_custom_categories: list[str]
    error: str | None


class UpdatedModelSettingsResponse(BaseModel):
    """Persisted model settings returned by PATCH."""

    object: Literal["model_settings"]
    omniharness_models: list[str]
    policy_model: str | None
    smart_routing_decision_model: str | None
    smart_routing_prompt: str | None
    smart_routing_cadence: Literal["per_turn", "first_turn_only"]
    turn_selection_user_message_count: int
    workload_classification_enabled: bool
    workload_custom_categories: list[str]


class OmniHarnessSettingsResponse(BaseModel):
    """User-facing editable OmniHarness settings."""

    object: Literal["omniharness_settings"]
    system_prompt: str
    prompt_profile_auto_include_limit: int


class UpdateOmniHarnessSettingsRequest(BaseModel):
    """Editable OmniHarness settings."""

    system_prompt: str | None = Field(default=None, max_length=100_000)
    prompt_profile_auto_include_limit: int | None = Field(default=None, ge=1)


def _databricks_profile(config: dict[str, Any]) -> str | None:
    caps = get_caps()
    if caps.llm is not None and caps.llm.profile:
        return caps.llm.profile
    auth = config.get("auth")
    if isinstance(auth, dict) and auth.get("type") == "databricks":
        profile = auth.get("profile")
        if isinstance(profile, str) and profile:
            return profile
    providers = config.get("providers")
    if isinstance(providers, dict):
        for provider in providers.values():
            if not isinstance(provider, dict) or provider.get("kind") != "databricks":
                continue
            profile = provider.get("profile")
            if isinstance(profile, str) and profile:
                return profile
    return None


def _serving_models(profile: str) -> list[ModelEntry]:
    """List ready chat endpoints, using their ``databricks-*`` wire ids."""
    creds = resolve_databricks_workspace(profile)
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"{creds.host.rstrip('/')}/api/2.0/serving-endpoints",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
        response.raise_for_status()
        payload = response.json()
    endpoints = payload.get("endpoints") if isinstance(payload, dict) else None
    entries: list[ModelEntry] = []
    for endpoint in endpoints if isinstance(endpoints, list) else []:
        if not isinstance(endpoint, dict):
            continue
        name = endpoint.get("name")
        task = endpoint.get("task")
        state = endpoint.get("state")
        ready = state.get("ready") if isinstance(state, dict) else None
        if (
            isinstance(name, str)
            and name
            and isinstance(task, str)
            and ("chat" in task.lower() or "completion" in task.lower())
            and (not isinstance(ready, str) or not ready or ready.upper() == "READY")
        ):
            entries.append(ModelEntry(id=name, family=model_family_token(name)))
    return sorted(entries, key=lambda entry: entry.id)


def _display_name(model_id: str) -> str:
    bare = model_id.removeprefix("databricks-").removeprefix("system.ai.")
    words = bare.replace("-", " ").split()
    acronyms = {"gpt", "glm", "ai", "oss", "qwen", "llama"}
    label = " ".join(
        word.upper() if word.lower() in acronyms else word.capitalize() for word in words
    )
    return re.sub(r"\b(\d+) (\d+)\b", r"\1.\2", label, count=1)


def configured_omniharness_model_options(
    model_settings_store: ModelSettingsStore,
) -> list[dict[str, Any]]:
    """Return the configured public picker rows for OmniHarness."""
    model_ids = model_settings_store.get().harness_models.get(OMNIHARNESS_AGENT_NAME, [])
    refresh_omniharness_model_catalog(model_ids)
    return [_model_option(model_id) for model_id in model_ids]


def _model_option(model_id: str) -> dict[str, Any]:
    """Serialize one model with Omnigent-owned runtime metadata."""
    metadata = get_omniharness_model_metadata(model_id)
    return {
        "id": model_id,
        "display_name": _display_name(model_id),
        "context_window": metadata.context_window,
        "context_window_is_estimate": metadata.context_window_is_estimate,
        "max_output_tokens": metadata.metadata.max_output_tokens,
        "capabilities": {
            capability.value: supported
            for supported, values in (
                (True, metadata.metadata.supported_capabilities),
                (False, metadata.metadata.unsupported_capabilities),
            )
            for capability in sorted(values, key=lambda value: value.value)
        },
        "pricing_status": "known" if metadata.pricing is not None else "unknown",
    }


async def _require_admin(
    request: Request,
    auth_provider: AuthProvider | None,
    permission_store: PermissionStore | None,
) -> None:
    if auth_provider is None:
        return
    user_id = get_user_id(request, auth_provider)
    if permission_store is None:
        return
    if user_id is None:
        raise OmnigentError("Authentication required", code=ErrorCode.UNAUTHORIZED)
    if not await asyncio.to_thread(permission_store.is_admin, user_id):
        raise OmnigentError(
            "Admin privileges required to manage model settings",
            code=ErrorCode.FORBIDDEN,
        )


def _set_runtime_policy_model(model: str | None, profile: str | None) -> None:
    caps = get_caps()
    if model is None:
        caps.llm = None
        return
    current = caps.llm
    caps.llm = LLMConfig(
        model=model,
        extra=dict(current.extra) if current else {},
        connection=dict(current.connection) if current and current.connection else None,
        profile=current.profile if current and current.profile else profile,
        request_timeout=current.request_timeout if current else 300,
        retry=current.retry if current else LLMConfig(model=model).retry,
        fallback_models=list(current.fallback_models) if current else [],
    )


def create_model_settings_router(
    model_settings_store: ModelSettingsStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
    server_config: dict[str, Any] | None = None,
) -> APIRouter:
    """Build model-option and admin model-settings routes."""
    router = APIRouter()
    config = server_config or {}

    @router.get("/model-options")
    async def model_options(request: Request) -> dict[str, Any]:
        require_user(request, auth_provider)
        return {
            "object": "list",
            "harness": OMNIHARNESS_AGENT_NAME,
            "data": await asyncio.to_thread(
                configured_omniharness_model_options,
                model_settings_store,
            ),
        }

    @router.get(
        "/omniharness/settings",
        response_model=OmniHarnessSettingsResponse,
    )
    async def get_omniharness_settings(request: Request) -> dict[str, Any]:
        await _require_admin(request, auth_provider, permission_store)
        settings = await asyncio.to_thread(model_settings_store.get)
        return {
            "object": "omniharness_settings",
            "system_prompt": settings.omniharness_system_prompt,
            "prompt_profile_auto_include_limit": settings.prompt_profile_auto_include_limit,
        }

    @router.patch(
        "/omniharness/settings",
        response_model=OmniHarnessSettingsResponse,
    )
    async def update_omniharness_settings(
        request: Request,
        body: UpdateOmniHarnessSettingsRequest,
    ) -> dict[str, Any]:
        await _require_admin(request, auth_provider, permission_store)
        settings = await asyncio.to_thread(
            model_settings_store.update,
            omniharness_system_prompt=body.system_prompt,
            update_omniharness_system_prompt="system_prompt" in body.model_fields_set,
            prompt_profile_auto_include_limit=body.prompt_profile_auto_include_limit,
            update_prompt_profile_auto_include_limit=(
                "prompt_profile_auto_include_limit" in body.model_fields_set
            ),
            updated_by=get_user_id(request, auth_provider),
        )
        return {
            "object": "omniharness_settings",
            "system_prompt": settings.omniharness_system_prompt,
            "prompt_profile_auto_include_limit": settings.prompt_profile_auto_include_limit,
        }

    @router.get(
        "/admin/model-settings",
        response_model=AdminModelSettingsResponse,
    )
    async def get_model_settings(request: Request) -> dict[str, Any]:
        await _require_admin(request, auth_provider, permission_store)
        settings = await asyncio.to_thread(model_settings_store.get)
        enabled = settings.harness_models.get(OMNIHARNESS_AGENT_NAME, [])
        profile = _databricks_profile(config)
        if profile is None:
            return {
                "object": "model_settings",
                "databricks_connected": False,
                "profile": None,
                "models": [],
                "omniharness_models": enabled,
                "policy_model": settings.policy_model,
                "smart_routing_decision_model": settings.smart_routing_decision_model,
                "smart_routing_prompt": settings.smart_routing_prompt,
                "smart_routing_cadence": settings.smart_routing_cadence,
                "turn_selection_user_message_count": settings.turn_selection_user_message_count,
                "workload_classification_enabled": settings.workload_classification_enabled,
                "workload_custom_categories": list(settings.workload_custom_categories),
                "error": None,
            }
        try:
            models = await asyncio.to_thread(_serving_models, profile)
            await asyncio.to_thread(
                refresh_omniharness_model_catalog,
                [model.id for model in models],
            )
        except (OSError, ValueError, httpx.HTTPError) as exc:
            return {
                "object": "model_settings",
                "databricks_connected": False,
                "profile": profile,
                "models": [],
                "omniharness_models": enabled,
                "policy_model": settings.policy_model,
                "smart_routing_decision_model": settings.smart_routing_decision_model,
                "smart_routing_prompt": settings.smart_routing_prompt,
                "smart_routing_cadence": settings.smart_routing_cadence,
                "turn_selection_user_message_count": settings.turn_selection_user_message_count,
                "workload_classification_enabled": settings.workload_classification_enabled,
                "workload_custom_categories": list(settings.workload_custom_categories),
                "error": str(exc),
            }
        return {
            "object": "model_settings",
            "databricks_connected": True,
            "profile": profile,
            "models": [_model_option(model.id) for model in models],
            "omniharness_models": enabled,
            "policy_model": settings.policy_model,
            "smart_routing_decision_model": settings.smart_routing_decision_model,
            "smart_routing_prompt": settings.smart_routing_prompt,
            "smart_routing_cadence": settings.smart_routing_cadence,
            "turn_selection_user_message_count": settings.turn_selection_user_message_count,
            "workload_classification_enabled": settings.workload_classification_enabled,
            "workload_custom_categories": list(settings.workload_custom_categories),
            "error": None,
        }

    @router.patch(
        "/admin/model-settings",
        response_model=UpdatedModelSettingsResponse,
    )
    async def update_model_settings(
        request: Request,
        body: UpdateModelSettingsRequest,
    ) -> dict[str, Any]:
        await _require_admin(request, auth_provider, permission_store)
        update_models = "omniharness_models" in body.model_fields_set
        update_policy_model = "policy_model" in body.model_fields_set
        update_decision_model = "smart_routing_decision_model" in body.model_fields_set
        update_prompt = "smart_routing_prompt" in body.model_fields_set
        update_cadence = "smart_routing_cadence" in body.model_fields_set
        update_message_count = "turn_selection_user_message_count" in body.model_fields_set
        update_workload_classification = "workload_classification_enabled" in body.model_fields_set
        update_workload_categories = "workload_custom_categories" in body.model_fields_set
        custom_categories = list(dict.fromkeys(body.workload_custom_categories or []))
        settings = await asyncio.to_thread(
            model_settings_store.update,
            harness=OMNIHARNESS_AGENT_NAME if update_models else None,
            enabled_models=(body.omniharness_models or []) if update_models else None,
            policy_model=body.policy_model or None,
            update_policy_model=update_policy_model,
            smart_routing_decision_model=body.smart_routing_decision_model or None,
            update_smart_routing_decision_model=update_decision_model,
            smart_routing_prompt=body.smart_routing_prompt,
            update_smart_routing_prompt=update_prompt,
            smart_routing_cadence=body.smart_routing_cadence,
            update_smart_routing_cadence=update_cadence,
            turn_selection_user_message_count=body.turn_selection_user_message_count,
            update_turn_selection_user_message_count=update_message_count,
            workload_custom_categories=custom_categories,
            update_workload_custom_categories=update_workload_categories,
            workload_classification_enabled=body.workload_classification_enabled,
            update_workload_classification_enabled=update_workload_classification,
            updated_by=get_user_id(request, auth_provider),
        )
        if update_policy_model:
            _set_runtime_policy_model(settings.policy_model, _databricks_profile(config))
        return {
            "object": "model_settings",
            "omniharness_models": settings.harness_models.get(OMNIHARNESS_AGENT_NAME, []),
            "policy_model": settings.policy_model,
            "smart_routing_decision_model": settings.smart_routing_decision_model,
            "smart_routing_prompt": settings.smart_routing_prompt,
            "smart_routing_cadence": settings.smart_routing_cadence,
            "turn_selection_user_message_count": settings.turn_selection_user_message_count,
            "workload_classification_enabled": settings.workload_classification_enabled,
            "workload_custom_categories": list(settings.workload_custom_categories),
        }

    return router
