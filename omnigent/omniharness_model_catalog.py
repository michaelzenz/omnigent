"""In-memory metadata catalog for models offered by OmniHarness."""

from __future__ import annotations

import importlib
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from omnigent.llms.context_window import (
    ModelPricing,
    fetch_model_pricing,
    lookup_model_context_window,
)
from omnigent.model_metadata import ModelCapability, ModelMetadata
from omnigent.onboarding.providers import find_catalog_models

_ESTIMATED_DATABRICKS_CONTEXT_WINDOW = 1_000_000
_LEGACY_CONTEXT_WINDOW = 128_000


class _LiteLLM(Protocol):
    def get_model_info(self, model: str) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class OmniHarnessModelMetadata:
    """Runtime facts used only by the Omnigent/OpenAI Agents harness."""

    model_id: str
    context_window: int
    context_window_source: str
    context_window_is_estimate: bool
    metadata: ModelMetadata
    pricing: ModelPricing | None
    pricing_source: str | None


_lock = threading.RLock()
_snapshot: dict[str, OmniHarnessModelMetadata] = {}


def _aliases(model: str) -> set[str]:
    """Return stable aliases used by Databricks and local metadata catalogs."""
    value = model.strip()
    aliases = {value, value.lower()}
    if value.startswith("databricks/"):
        bare = value.removeprefix("databricks/")
        aliases.update({bare, bare.lower()})
    elif value.startswith("databricks-"):
        aliases.update({f"databricks/{value}", f"databricks/{value}".lower()})
    return aliases


def _litellm_pricing(model: str) -> ModelPricing | None:
    """Read per-token prices from LiteLLM when MLflow has no price."""
    try:
        litellm = cast(_LiteLLM, importlib.import_module("litellm"))
    except ImportError:
        return None
    candidates = [model]
    if model.startswith("databricks-"):
        candidates.append(f"databricks/{model}")
    for candidate in candidates:
        try:
            info = litellm.get_model_info(candidate)
        except Exception:  # noqa: BLE001 - optional third-party metadata is best effort.
            continue
        input_price = info.get("input_cost_per_token") if info else None
        output_price = info.get("output_cost_per_token") if info else None
        if not isinstance(input_price, (int, float)) or not isinstance(output_price, (int, float)):
            continue
        cache_read = info.get("cache_read_input_token_cost")
        cache_write = info.get("cache_creation_input_token_cost")
        return ModelPricing(
            input_per_token=float(input_price),
            output_per_token=float(output_price),
            cache_read_per_token=(
                float(cache_read) if isinstance(cache_read, (int, float)) else None
            ),
            cache_write_per_token=(
                float(cache_write) if isinstance(cache_write, (int, float)) else None
            ),
        )
    return None


def _catalog_metadata(model: str) -> ModelMetadata:
    """Merge unambiguous capability/output facts from matching MLflow rows."""
    matches = find_catalog_models(model)

    def _agreed(attribute: str) -> object | None:
        values = {
            value
            for candidate in matches
            if (value := getattr(candidate, attribute, None)) is not None
        }
        return next(iter(values)) if len(values) == 1 else None

    supported: set[ModelCapability] = set()
    unsupported: set[ModelCapability] = set()
    for attribute, capability in (
        ("supports_function_calling", ModelCapability.TOOL_USE),
        ("supports_reasoning", ModelCapability.REASONING),
        ("supports_vision", ModelCapability.VISION),
        ("supports_structured_output", ModelCapability.STRUCTURED_OUTPUT),
    ):
        value = _agreed(attribute)
        if value is True:
            supported.add(capability)
        elif value is False:
            unsupported.add(capability)
    max_output_tokens = _agreed("max_output_tokens")
    return ModelMetadata(
        supported_capabilities=frozenset(supported),
        unsupported_capabilities=frozenset(unsupported),
        max_output_tokens=(
            int(max_output_tokens) if isinstance(max_output_tokens, (int, float)) else None
        ),
    )


def _litellm_metadata(model: str) -> ModelMetadata:
    """Return optional LiteLLM capability/output metadata."""
    try:
        litellm = cast(_LiteLLM, importlib.import_module("litellm"))
    except ImportError:
        return ModelMetadata()
    candidates = [model]
    if model.startswith("databricks-"):
        candidates.append(f"databricks/{model}")
    info: Mapping[str, object] = {}
    for candidate in candidates:
        try:
            info = litellm.get_model_info(candidate)
        except Exception:  # noqa: BLE001 - optional third-party metadata is best effort.
            continue
        if info:
            break
    supported: set[ModelCapability] = set()
    unsupported: set[ModelCapability] = set()
    for keys, capability in (
        (("supports_function_calling",), ModelCapability.TOOL_USE),
        (("supports_reasoning",), ModelCapability.REASONING),
        (("supports_vision",), ModelCapability.VISION),
        (
            ("supports_response_schema", "supports_structured_output"),
            ModelCapability.STRUCTURED_OUTPUT,
        ),
    ):
        value = next((info[key] for key in keys if key in info), None)
        if value is True:
            supported.add(capability)
        elif value is False:
            unsupported.add(capability)
    output_limit = info.get("max_output_tokens")
    return ModelMetadata(
        supported_capabilities=frozenset(supported),
        unsupported_capabilities=frozenset(unsupported),
        max_output_tokens=(
            int(output_limit)
            if isinstance(output_limit, (int, float, str)) and output_limit
            else None
        ),
    )


def _resolve(model: str) -> OmniHarnessModelMetadata:
    window, window_source = lookup_model_context_window(model)
    estimated = window is None
    if window is None:
        if model.startswith(("databricks-", "databricks/", "system.ai.")):
            window = _ESTIMATED_DATABRICKS_CONTEXT_WINDOW
        else:
            window = _LEGACY_CONTEXT_WINDOW
        window_source = "estimate"
    catalog_metadata = _catalog_metadata(model)
    litellm_metadata = _litellm_metadata(model)
    supported = catalog_metadata.supported_capabilities | (
        litellm_metadata.supported_capabilities - catalog_metadata.unsupported_capabilities
    )
    unsupported = catalog_metadata.unsupported_capabilities | (
        litellm_metadata.unsupported_capabilities - catalog_metadata.supported_capabilities
    )
    metadata = ModelMetadata(
        supported_capabilities=supported,
        unsupported_capabilities=unsupported,
        context_window=window,
        max_output_tokens=(
            catalog_metadata.max_output_tokens or litellm_metadata.max_output_tokens
        ),
        cost_tier=catalog_metadata.cost_tier,
        wire_apis=catalog_metadata.wire_apis,
        reasoning=catalog_metadata.reasoning,
    )

    pricing = fetch_model_pricing(model)
    pricing_source: str | None = "mlflow" if pricing is not None else None
    if pricing is None:
        pricing = _litellm_pricing(model)
        if pricing is not None:
            pricing_source = "litellm"

    return OmniHarnessModelMetadata(
        model_id=model,
        context_window=window,
        context_window_source=window_source or "estimate",
        context_window_is_estimate=estimated,
        metadata=metadata,
        pricing=pricing,
        pricing_source=pricing_source,
    )


def refresh_omniharness_model_catalog(model_ids: Iterable[str]) -> None:
    """Atomically refresh metadata for every model returned by discovery."""
    refreshed: dict[str, OmniHarnessModelMetadata] = {}
    for model_id in dict.fromkeys(model_ids):
        metadata = _resolve(model_id)
        for alias in _aliases(model_id):
            refreshed[alias] = metadata
    with _lock:
        _snapshot.clear()
        _snapshot.update(refreshed)


def get_omniharness_model_metadata(model: str) -> OmniHarnessModelMetadata:
    """Return cached metadata, resolving and retaining a cache miss."""
    with _lock:
        for alias in _aliases(model):
            metadata = _snapshot.get(alias)
            if metadata is not None:
                return metadata
    metadata = _resolve(model)
    with _lock:
        for alias in _aliases(model):
            _snapshot[alias] = metadata
    return metadata


def find_omniharness_model_metadata(model: str) -> OmniHarnessModelMetadata | None:
    """Return cached Omnigent metadata without claiming an unrelated model."""
    with _lock:
        for alias in _aliases(model):
            metadata = _snapshot.get(alias)
            if metadata is not None:
                return metadata
    return None


def get_omniharness_context_window(model: str) -> int:
    """Return the effective OmniHarness context window."""
    return get_omniharness_model_metadata(model).context_window


def get_omniharness_model_pricing(model: str) -> ModelPricing | None:
    """Return known OmniHarness pricing, or ``None`` when unknown."""
    return get_omniharness_model_metadata(model).pricing
