"""Tests for OmniHarness's runtime model metadata snapshot."""

from __future__ import annotations

import pytest

from omnigent import omniharness_model_catalog as catalog
from omnigent.llms.context_window import ModelPricing
from omnigent.model_metadata import ModelCapability, ModelMetadata


@pytest.fixture(autouse=True)
def _clear_catalog() -> None:
    catalog.refresh_omniharness_model_catalog([])


def test_unknown_databricks_model_uses_flagged_one_million_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(catalog, "lookup_model_context_window", lambda _model: (None, None))
    monkeypatch.setattr(catalog, "fetch_model_pricing", lambda _model: None)
    monkeypatch.setattr(catalog, "_litellm_pricing", lambda _model: None)
    monkeypatch.setattr(catalog, "_catalog_metadata", lambda _model: ModelMetadata())
    monkeypatch.setattr(catalog, "_litellm_metadata", lambda _model: ModelMetadata())

    metadata = catalog.get_omniharness_model_metadata("databricks-kimi-k3")

    assert metadata.context_window == 1_000_000
    assert metadata.context_window_is_estimate is True
    assert metadata.context_window_source == "estimate"
    assert metadata.pricing is None


def test_refresh_keeps_verified_metadata_and_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing = ModelPricing(input_per_token=1e-6, output_per_token=2e-6)
    monkeypatch.setattr(
        catalog,
        "lookup_model_context_window",
        lambda _model: (262_144, "mlflow"),
    )
    monkeypatch.setattr(catalog, "fetch_model_pricing", lambda _model: pricing)
    monkeypatch.setattr(
        catalog,
        "_catalog_metadata",
        lambda _model: ModelMetadata(
            supported_capabilities=frozenset({ModelCapability.TOOL_USE}),
            max_output_tokens=32_768,
        ),
    )
    monkeypatch.setattr(catalog, "_litellm_metadata", lambda _model: ModelMetadata())

    catalog.refresh_omniharness_model_catalog(["databricks-glm-5-2"])

    metadata = catalog.get_omniharness_model_metadata("databricks/databricks-glm-5-2")
    assert metadata.context_window == 262_144
    assert metadata.context_window_is_estimate is False
    assert metadata.metadata.max_output_tokens == 32_768
    assert metadata.metadata.supports(ModelCapability.TOOL_USE) is True
    assert metadata.pricing is pricing
    assert catalog.find_omniharness_model_metadata("databricks-glm-5-2") is metadata
