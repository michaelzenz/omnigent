"""Shared Omnigent per-request usage recording helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from omnigent.llms.context_window import ModelPricing, compute_llm_cost
from omnigent.omnigent_model_catalog import get_omnigent_model_metadata
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores import ConversationStore

_CACHE_READ_RATIO = 0.10
_CACHE_WRITE_RATIO = 1.25
_logger = logging.getLogger(__name__)


def canonical_purpose(components: Iterable[str]) -> str:
    """Return a stable purpose for one consolidated model request."""
    return "+".join(sorted(set(components)))


def response_usage(response: object) -> dict[str, int]:
    """Normalize Responses SDK usage objects or plain usage mappings."""
    raw = getattr(response, "usage", None)
    if raw is None and isinstance(response, dict):
        raw = response.get("usage")

    def _value(name: str) -> int:
        value = raw.get(name, 0) if isinstance(raw, dict) else getattr(raw, name, 0)
        return int(value or 0)

    return {
        "input_tokens": _value("input_tokens"),
        "output_tokens": _value("output_tokens"),
        "cache_read_input_tokens": _value("cache_read_input_tokens"),
        "cache_creation_input_tokens": _value("cache_creation_input_tokens"),
    }


def _price_snapshot(pricing: ModelPricing | None) -> dict[str, float | None]:
    if pricing is None:
        return {
            "input_price_per_token": None,
            "output_price_per_token": None,
            "cache_read_price_per_token": None,
            "cache_write_price_per_token": None,
        }
    return {
        "input_price_per_token": pricing.input_per_token,
        "output_price_per_token": pricing.output_per_token,
        "cache_read_price_per_token": (
            pricing.cache_read_per_token
            if pricing.cache_read_per_token is not None
            else pricing.input_per_token * _CACHE_READ_RATIO
        ),
        "cache_write_price_per_token": (
            pricing.cache_write_per_token
            if pricing.cache_write_per_token is not None
            else pricing.input_per_token * _CACHE_WRITE_RATIO
        ),
    }


def record_omnigent_usage(
    conversation_store: ConversationStore,
    *,
    session_id: str,
    turn_id: str | None,
    purpose: str,
    model: str | None,
    workload: str | None,
    usage: dict[str, Any],
    provider_cost: float | None = None,
) -> None:
    """Append one ledger row without modifying legacy usage rollups."""
    try:
        owner = conversation_store.get_session_owner(session_id) or RESERVED_USER_LOCAL
        metadata = get_omnigent_model_metadata(model) if model else None
        service_pricing = metadata.pricing if metadata else None
        override = (
            conversation_store.get_model_pricing_override(owner, model)
            if model is not None
            else None
        )
        pricing = (
            ModelPricing(
                input_per_token=float(override["input_price_per_token"]),
                output_per_token=float(override["output_price_per_token"]),
                cache_read_per_token=override["cache_read_price_per_token"],
                cache_write_per_token=override["cache_write_price_per_token"],
            )
            if override is not None
            else service_pricing
        )
        use_provider_cost = override is None and provider_cost is not None
        priced = use_provider_cost or pricing is not None
        cost = (
            float(provider_cost)
            if use_provider_cost
            else (compute_llm_cost(usage, pricing) if pricing is not None else None)
        )
        pricing_source = (
            "custom"
            if override is not None
            else (
                "provider" if use_provider_cost else metadata.pricing_source if metadata else None
            )
        )
        conversation_store.record_usage_ledger(
            {
                "user_id": owner,
                "session_id": session_id,
                "turn_id": turn_id,
                "purpose": purpose,
                "model": model,
                "workload": workload,
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
                "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0),
                **_price_snapshot(pricing),
                "pricing_source": pricing_source,
                "cost_usd": cost,
                "priced": priced,
            }
        )
    except (OSError, RuntimeError, ValueError, NotImplementedError):
        _logger.warning(
            "Omnigent usage ledger write failed for session=%s purpose=%s",
            session_id,
            purpose,
            exc_info=True,
        )


__all__ = [
    "canonical_purpose",
    "record_omnigent_usage",
    "response_usage",
]
