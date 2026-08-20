"""Monthly, user-scoped Omnigent request statistics."""

from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field

from omnigent.db.utils import now_epoch
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.llms.context_window import ModelPricing
from omnigent.omnigent_model_catalog import get_omnigent_model_metadata
from omnigent.server.auth import RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores import ConversationStore
from omnigent.stores.model_settings_store import ModelSettingsStore

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class StatisticsBucket(BaseModel):
    """One daily or categorical usage bucket."""

    key: str
    calls: int
    priced_calls: int
    unpriced_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    total_tokens: int
    cost_usd: float


class StatisticsPricing(BaseModel):
    """Current enabled Omnigent model price, separate from historical snapshots."""

    model: str
    pricing_status: Literal["known", "unknown"]
    pricing_source: str | None
    input_price_per_token: float | None
    output_price_per_token: float | None
    cache_read_price_per_token: float | None
    cache_write_price_per_token: float | None
    service_pricing_status: Literal["known", "unknown"]
    service_pricing_source: str | None
    service_input_price_per_token: float | None
    service_output_price_per_token: float | None
    service_cache_read_price_per_token: float | None
    service_cache_write_price_per_token: float | None
    custom_input_price_per_token: float | None
    custom_output_price_per_token: float | None
    custom_cache_read_price_per_token: float | None
    custom_cache_write_price_per_token: float | None
    effective_input_price_per_token: float | None
    effective_output_price_per_token: float | None
    effective_cache_read_price_per_token: float | None
    effective_cache_write_price_per_token: float | None
    has_custom_pricing: bool
    custom_differs_from_service: bool


class ModelPricingOverrideRequest(BaseModel):
    """Human-facing prices in USD per million tokens."""

    input_price_per_million: float
    output_price_per_million: float
    cache_read_price_per_million: float | None = Field(default=None)
    cache_write_price_per_million: float | None = Field(default=None)


class ModelPricingOverrideResponse(BaseModel):
    """Saved custom pricing in the same per-million units accepted by PUT."""

    object: Literal["model_pricing_override"]
    model: str
    input_price_per_million: float
    output_price_per_million: float
    cache_read_price_per_million: float | None
    cache_write_price_per_million: float | None
    updated_at: int


class StatisticsResponse(BaseModel):
    """Frontend contract for one UTC calendar month."""

    object: Literal["statistics"]
    month: str
    available_months: list[str]
    workload_classification_enabled: bool
    totals: StatisticsBucket
    daily: list[StatisticsBucket]
    by_model: list[StatisticsBucket]
    by_purpose: list[StatisticsBucket]
    by_workload: list[StatisticsBucket]
    current_pricing: list[StatisticsPricing]


def _bucket(key: str) -> dict[str, Any]:
    return {
        "key": key,
        "calls": 0,
        "priced_calls": 0,
        "unpriced_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }


def _add(target: dict[str, Any], row: dict[str, Any]) -> None:
    target["calls"] += 1
    target["priced_calls" if row["priced"] else "unpriced_calls"] += 1
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        target[field] += int(row[field] or 0)
    target["total_tokens"] += sum(
        int(row[field] or 0)
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )
    if row["cost_usd"] is not None:
        target["cost_usd"] += float(row["cost_usd"])


def _price_values(pricing: ModelPricing | None) -> dict[str, float | None]:
    if pricing is None:
        return {
            "input": None,
            "output": None,
            "cache_read": None,
            "cache_write": None,
        }
    input_price = float(pricing.input_per_token)
    return {
        "input": input_price,
        "output": float(pricing.output_per_token),
        "cache_read": (
            float(cache_read)
            if (cache_read := pricing.cache_read_per_token) is not None
            else input_price * 0.10
        ),
        "cache_write": (
            float(cache_write)
            if (cache_write := pricing.cache_write_per_token) is not None
            else input_price * 1.25
        ),
    }


def _custom_price_values(override: dict[str, Any] | None) -> dict[str, float | None]:
    if override is None:
        return _price_values(None)
    input_price = float(override["input_price_per_token"])
    cache_read = override["cache_read_price_per_token"]
    cache_write = override["cache_write_price_per_token"]
    return {
        "input": input_price,
        "output": float(override["output_price_per_token"]),
        "cache_read": float(cache_read) if cache_read is not None else input_price * 0.10,
        "cache_write": float(cache_write) if cache_write is not None else input_price * 1.25,
    }


def _prices_differ(
    service: dict[str, float | None],
    custom: dict[str, float | None],
) -> bool:
    return any(
        service[name] is None
        or custom[name] is None
        or not math.isclose(
            float(service[name]),
            float(custom[name]),
            rel_tol=1e-9,
            abs_tol=1e-15,
        )
        for name in ("input", "output", "cache_read", "cache_write")
    )


def _pricing(
    conversation_store: ConversationStore,
    model_settings_store: ModelSettingsStore | None,
    user_id: str,
) -> tuple[bool, list[dict[str, Any]]]:
    if model_settings_store is None:
        return False, []
    settings = model_settings_store.get()
    models = settings.harness_models.get("openai-agents", [])
    overrides = conversation_store.list_model_pricing_overrides(user_id, models)
    result: list[dict[str, Any]] = []
    for model in models:
        metadata = get_omnigent_model_metadata(model)
        service = _price_values(metadata.pricing)
        override = overrides.get(model)
        custom = _custom_price_values(override)
        effective = custom if override is not None else service
        service_known = metadata.pricing is not None
        result.append(
            {
                "model": model,
                # Legacy fields remain the effective view consumed by the current UI.
                "pricing_status": "known" if override is not None or service_known else "unknown",
                "pricing_source": "custom" if override is not None else metadata.pricing_source,
                "input_price_per_token": effective["input"],
                "output_price_per_token": effective["output"],
                "cache_read_price_per_token": effective["cache_read"],
                "cache_write_price_per_token": effective["cache_write"],
                "service_pricing_status": "known" if service_known else "unknown",
                "service_pricing_source": metadata.pricing_source,
                "service_input_price_per_token": service["input"],
                "service_output_price_per_token": service["output"],
                "service_cache_read_price_per_token": service["cache_read"],
                "service_cache_write_price_per_token": service["cache_write"],
                "custom_input_price_per_token": (
                    override["input_price_per_token"] if override is not None else None
                ),
                "custom_output_price_per_token": (
                    override["output_price_per_token"] if override is not None else None
                ),
                "custom_cache_read_price_per_token": (
                    override["cache_read_price_per_token"] if override is not None else None
                ),
                "custom_cache_write_price_per_token": (
                    override["cache_write_price_per_token"] if override is not None else None
                ),
                "effective_input_price_per_token": effective["input"],
                "effective_output_price_per_token": effective["output"],
                "effective_cache_read_price_per_token": effective["cache_read"],
                "effective_cache_write_price_per_token": effective["cache_write"],
                "has_custom_pricing": override is not None,
                "custom_differs_from_service": (
                    _prices_differ(service, custom)
                    if override is not None and service_known
                    else False
                ),
            }
        )
    return settings.workload_classification_enabled, result


def _build_statistics(
    conversation_store: ConversationStore,
    model_settings_store: ModelSettingsStore | None,
    user_id: str | None,
    month: str,
) -> StatisticsResponse:
    owner = user_id or RESERVED_USER_LOCAL
    rows = conversation_store.list_usage_ledger_month(owner, month)
    totals = _bucket("total")
    dimensions: dict[str, defaultdict[str, dict[str, Any]]] = {
        name: defaultdict(lambda: _bucket(""))
        for name in ("day_utc", "model", "purpose", "workload")
    }
    for row in rows:
        _add(totals, row)
        for dimension in dimensions:
            raw_key = row.get(dimension)
            key = str(raw_key) if raw_key else "unclassified"
            bucket = dimensions[dimension][key]
            bucket["key"] = key
            _add(bucket, row)
    enabled, pricing = _pricing(conversation_store, model_settings_store, owner)

    def _values(name: str, *, chronological: bool = False) -> list[StatisticsBucket]:
        values = list(dimensions[name].values())
        values.sort(
            key=(lambda item: item["key"])
            if chronological
            else (lambda item: (-item["cost_usd"], -item["total_tokens"], item["key"]))
        )
        return [StatisticsBucket(**value) for value in values]

    return StatisticsResponse(
        object="statistics",
        month=month,
        available_months=conversation_store.list_usage_ledger_months(owner),
        workload_classification_enabled=enabled,
        totals=StatisticsBucket(**totals),
        daily=_values("day_utc", chronological=True),
        by_model=_values("model"),
        by_purpose=_values("purpose"),
        by_workload=_values("workload"),
        current_pricing=[StatisticsPricing(**item) for item in pricing],
    )


def _override_response(row: dict[str, Any]) -> ModelPricingOverrideResponse:
    per_million = 1_000_000
    cache_read = row["cache_read_price_per_token"]
    cache_write = row["cache_write_price_per_token"]
    return ModelPricingOverrideResponse(
        object="model_pricing_override",
        model=str(row["model"]),
        input_price_per_million=float(row["input_price_per_token"]) * per_million,
        output_price_per_million=float(row["output_price_per_token"]) * per_million,
        cache_read_price_per_million=(
            float(cache_read) * per_million if cache_read is not None else None
        ),
        cache_write_price_per_million=(
            float(cache_write) * per_million if cache_write is not None else None
        ),
        updated_at=int(row["updated_at"]),
    )


def create_statistics_router(
    conversation_store: ConversationStore,
    *,
    model_settings_store: ModelSettingsStore | None = None,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Create the authenticated, user-scoped statistics route."""
    router = APIRouter()

    @router.get("/statistics", response_model=StatisticsResponse)
    async def get_statistics(
        request: Request,
        month: str | None = Query(default=None),
    ) -> StatisticsResponse:
        user_id = require_user(request, auth_provider)
        selected = month or datetime.fromtimestamp(now_epoch(), tz=timezone.utc).strftime("%Y-%m")
        if not _MONTH_RE.fullmatch(selected):
            raise OmnigentError(
                "month must use YYYY-MM format",
                code=ErrorCode.INVALID_INPUT,
            )
        return await asyncio.to_thread(
            _build_statistics,
            conversation_store,
            model_settings_store,
            user_id,
            selected,
        )

    @router.put(
        "/statistics/model-pricing/{model:path}",
        response_model=ModelPricingOverrideResponse,
    )
    async def set_model_pricing(
        request: Request,
        model: str,
        body: ModelPricingOverrideRequest,
    ) -> ModelPricingOverrideResponse:
        user_id = require_user(request, auth_provider)
        owner = user_id or RESERVED_USER_LOCAL
        if not model.strip() or len(model) > 300:
            raise OmnigentError(
                "model must contain 1 to 300 characters",
                code=ErrorCode.INVALID_INPUT,
            )
        prices = (
            body.input_price_per_million,
            body.output_price_per_million,
            body.cache_read_price_per_million,
            body.cache_write_price_per_million,
        )
        if any(value is not None and (not math.isfinite(value) or value < 0) for value in prices):
            raise OmnigentError(
                "prices must be finite and nonnegative",
                code=ErrorCode.INVALID_INPUT,
            )
        per_token = {
            "input_price_per_token": body.input_price_per_million / 1_000_000,
            "output_price_per_token": body.output_price_per_million / 1_000_000,
            "cache_read_price_per_token": (
                body.cache_read_price_per_million / 1_000_000
                if body.cache_read_price_per_million is not None
                else None
            ),
            "cache_write_price_per_token": (
                body.cache_write_price_per_million / 1_000_000
                if body.cache_write_price_per_million is not None
                else None
            ),
        }
        try:
            saved = await asyncio.to_thread(
                conversation_store.set_model_pricing_override,
                owner,
                model,
                per_token,
            )
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        return _override_response(saved)

    @router.delete(
        "/statistics/model-pricing/{model:path}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def clear_model_pricing(
        request: Request,
        model: str,
    ) -> Response:
        user_id = require_user(request, auth_provider)
        owner = user_id or RESERVED_USER_LOCAL
        await asyncio.to_thread(
            conversation_store.delete_model_pricing_override,
            owner,
            model,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


__all__ = [
    "ModelPricingOverrideRequest",
    "ModelPricingOverrideResponse",
    "StatisticsBucket",
    "StatisticsPricing",
    "StatisticsResponse",
    "create_statistics_router",
]
