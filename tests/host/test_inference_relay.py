from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request

from omnigent.host.inference_relay import HostInferenceRelay, _surface_and_path


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }
    )


def test_relay_capability_is_runner_scoped_and_revocable() -> None:
    relay = HostInferenceRelay("http://server")
    relay._port = 43127
    relay._server = SimpleNamespace(started=True)  # type: ignore[assignment]
    relay._server_task = SimpleNamespace(done=lambda: False)  # type: ignore[assignment]

    endpoint = relay.register(
        session_id="conv_1",
        runner_id="runner_1",
        binding_token="binding-secret",
    )

    binding = relay._authorize(_request(endpoint.capability))
    assert binding.session_id == "conv_1"
    assert binding.runner_id == "runner_1"
    relay.revoke("runner_1")
    assert relay._bindings == {}


def test_relay_maps_only_v1_surface_prefixes() -> None:
    assert _surface_and_path("ai-gateway/anthropic/v1/messages") == (
        "anthropic",
        "v1/messages",
    )
    assert _surface_and_path("ai-gateway/codex/v1/responses") == (
        "responses",
        "responses",
    )
    assert _surface_and_path("serving-endpoints/chat/completions") == (
        "completions",
        "chat/completions",
    )
