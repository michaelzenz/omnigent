"""Shared constants for server-brokered Onih inference."""

from __future__ import annotations

from typing import Final, Literal

from omnigent.pi_model_compatibility import (
    DatabricksPiSurface,
    databricks_pi_surface_for_model,
)

HOST_INFERENCE_PROXY_URL_ENV: Final = "OMNIGENT_INFERENCE_PROXY_URL"
HOST_INFERENCE_PROXY_TOKEN_ENV: Final = "OMNIGENT_INFERENCE_PROXY_TOKEN"
PI_INFERENCE_PROXY_TOKEN_ENV: Final = "PI_OMNIGENT_INFERENCE_PROXY_TOKEN"
HARNESS_PI_SERVER_PROXY_ENV: Final = "HARNESS_PI_SERVER_PROXY"

InferenceSurface = Literal["anthropic", "responses", "completions"]


def inference_surface_for_model(model: str) -> InferenceSurface:
    """Return the broker surface for an Onih Pi model."""
    surface = databricks_pi_surface_for_model(model)
    if surface is DatabricksPiSurface.ANTHROPIC:
        return "anthropic"
    if surface is DatabricksPiSurface.RESPONSES:
        return "responses"
    if surface is DatabricksPiSurface.COMPLETIONS:
        return "completions"
    raise ValueError(f"server-brokered Pi does not support the MLflow surface: {model}")
