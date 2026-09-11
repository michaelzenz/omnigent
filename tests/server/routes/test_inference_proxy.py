from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import FastAPI, HTTPException

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER, token_bound_runner_id
from omnigent.server.routes import inference_proxy
from omnigent.server.routes.inference_proxy import (
    _validated_workspace_origin,
    create_inference_proxy_router,
)


def test_workspace_origin_rejects_non_databricks_destination() -> None:
    with pytest.raises(HTTPException):
        _validated_workspace_origin("http://127.0.0.1:8000")


def test_profile_auth_cache_includes_workspace_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def resolve(profile: str) -> tuple[_Auth, str]:
        calls.append(profile)
        return _Auth(), "https://dbc-test.cloud.databricks.com"

    inference_proxy._profile_auth.cache_clear()
    monkeypatch.setattr(inference_proxy, "_resolve_databricks_auth", resolve)
    try:
        inference_proxy._profile_auth("profile", "https://one.cloud.databricks.com")
        inference_proxy._profile_auth("profile", "https://two.cloud.databricks.com")
    finally:
        inference_proxy._profile_auth.cache_clear()

    assert calls == ["profile", "profile"]


class _ConversationStore:
    def __init__(self, conversation: object | None) -> None:
        self.conversation = conversation

    def get_conversation(self, session_id: str) -> object | None:
        del session_id
        return self.conversation


class _Auth:
    def current_token(self) -> str:
        return "databricks-secret"


@pytest.mark.asyncio
@respx.mock
async def test_proxy_binds_runner_and_injects_server_databricks_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding_token = "runner-binding-secret"
    runner_id = token_bound_runner_id(binding_token)
    store = _ConversationStore(SimpleNamespace(runner_id=runner_id, host_id="host_1"))
    monkeypatch.setattr(
        inference_proxy,
        "default_provider_for_harness",
        lambda _config, _harness: SimpleNamespace(
            kind=inference_proxy.DATABRICKS_KIND,
            profile="local-profile",
        ),
    )
    monkeypatch.setattr(inference_proxy, "load_config", dict)
    monkeypatch.setattr(
        inference_proxy,
        "_profile_auth",
        lambda _profile, _workspace_origin: (
            _Auth(),
            "https://dbc-test.cloud.databricks.com",
        ),
    )
    monkeypatch.setattr(
        inference_proxy,
        "get_workspace_url_for_profile",
        lambda _profile: "https://dbc-test.cloud.databricks.com",
    )
    upstream = respx.post(
        "https://dbc-test.cloud.databricks.com/ai-gateway/openai/v1/responses"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    app = FastAPI()
    app.include_router(create_inference_proxy_router(store, enabled=True), prefix="/v1")  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://server",
    ) as client:
        response = await client.post(
            (f"/v1/runners/{runner_id}/sessions/conv_1/inference/responses/responses"),
            headers={
                RUNNER_TUNNEL_TOKEN_HEADER: binding_token,
                "authorization": "Bearer must-not-forward",
            },
            json={"model": "system.ai.glm-5-2"},
        )

    assert response.status_code == 200
    assert upstream.called
    assert upstream.calls.last.request.headers["authorization"] == ("Bearer databricks-secret")


@pytest.mark.asyncio
@respx.mock
async def test_proxy_routes_databricks_glm_alias_to_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding_token = "runner-binding-secret"
    runner_id = token_bound_runner_id(binding_token)
    store = _ConversationStore(SimpleNamespace(runner_id=runner_id, host_id="host_1"))
    monkeypatch.setattr(
        inference_proxy,
        "default_provider_for_harness",
        lambda _config, _harness: SimpleNamespace(
            kind=inference_proxy.DATABRICKS_KIND,
            profile="local-profile",
        ),
    )
    monkeypatch.setattr(inference_proxy, "load_config", dict)
    monkeypatch.setattr(
        inference_proxy,
        "_profile_auth",
        lambda _profile, _workspace_origin: (
            _Auth(),
            "https://dbc-test.cloud.databricks.com",
        ),
    )
    monkeypatch.setattr(
        inference_proxy,
        "get_workspace_url_for_profile",
        lambda _profile: "https://dbc-test.cloud.databricks.com",
    )
    upstream = respx.post(
        "https://dbc-test.cloud.databricks.com/serving-endpoints/chat/completions"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    app = FastAPI()
    app.include_router(create_inference_proxy_router(store, enabled=True), prefix="/v1")  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://server",
    ) as client:
        response = await client.post(
            (f"/v1/runners/{runner_id}/sessions/conv_1/inference/completions/chat/completions"),
            headers={RUNNER_TUNNEL_TOKEN_HEADER: binding_token},
            json={"model": "databricks-glm-5-2"},
        )

    assert response.status_code == 200
    assert upstream.called


@pytest.mark.asyncio
@respx.mock
async def test_proxy_relays_and_logs_upstream_error_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    binding_token = "runner-binding-secret"
    runner_id = token_bound_runner_id(binding_token)
    store = _ConversationStore(SimpleNamespace(runner_id=runner_id, host_id="host_1"))
    monkeypatch.setattr(
        inference_proxy,
        "default_provider_for_harness",
        lambda _config, _harness: SimpleNamespace(
            kind=inference_proxy.DATABRICKS_KIND,
            profile="local-profile",
        ),
    )
    monkeypatch.setattr(inference_proxy, "load_config", dict)
    monkeypatch.setattr(
        inference_proxy,
        "_profile_auth",
        lambda _profile, _workspace_origin: (
            _Auth(),
            "https://dbc-test.cloud.databricks.com",
        ),
    )
    monkeypatch.setattr(
        inference_proxy,
        "get_workspace_url_for_profile",
        lambda _profile: "https://dbc-test.cloud.databricks.com",
    )
    upstream_error = {
        "error_code": "BAD_REQUEST",
        "message": "Requested token count exceeds the model's maximum context length",
    }
    respx.post("https://dbc-test.cloud.databricks.com/serving-endpoints/chat/completions").mock(
        return_value=httpx.Response(400, json=upstream_error)
    )
    app = FastAPI()
    app.include_router(create_inference_proxy_router(store, enabled=True), prefix="/v1")  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="omnigent.server.routes.inference_proxy"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://server",
        ) as client:
            response = await client.post(
                (
                    f"/v1/runners/{runner_id}/sessions/conv_1/inference/completions/chat/completions"
                ),
                headers={RUNNER_TUNNEL_TOKEN_HEADER: binding_token},
                json={"model": "databricks-glm-5-3-flash"},
            )

    assert response.status_code == 400
    # The gateway body ({error_code, message}) is re-wrapped into the OpenAI
    # error envelope — the OpenAI SDK pi uses reads only body['error'] and
    # would otherwise surface the failure as "400 status code (no body)".
    wrapped = response.json()
    assert wrapped["error"]["type"] == "upstream_error"
    assert "Requested token count exceeds" in wrapped["error"]["message"]
    assert "error_code" in wrapped["error"]["message"]
    assert "Requested token count exceeds" in caplog.text
    assert "databricks-glm-5-3-flash" in caplog.text


@pytest.mark.asyncio
@respx.mock
async def test_proxy_decodes_gzipped_upstream_error_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A gzip-encoded gateway error is decoded, logged plainly, and re-wrapped.

    The gateway gzips error responses when the client offers gzip (httpx
    always does); the proxy must log the plaintext and return the re-wrapped
    envelope as plain JSON, not forward opaque compressed bytes.
    """
    binding_token = "runner-binding-secret"
    runner_id = token_bound_runner_id(binding_token)
    store = _ConversationStore(SimpleNamespace(runner_id=runner_id, host_id="host_1"))
    monkeypatch.setattr(
        inference_proxy,
        "default_provider_for_harness",
        lambda _config, _harness: SimpleNamespace(
            kind=inference_proxy.DATABRICKS_KIND,
            profile="local-profile",
        ),
    )
    monkeypatch.setattr(inference_proxy, "load_config", dict)
    monkeypatch.setattr(
        inference_proxy,
        "_profile_auth",
        lambda _profile, _workspace_origin: (
            _Auth(),
            "https://dbc-test.cloud.databricks.com",
        ),
    )
    monkeypatch.setattr(
        inference_proxy,
        "get_workspace_url_for_profile",
        lambda _profile: "https://dbc-test.cloud.databricks.com",
    )
    import gzip as _gzip

    upstream_error = {
        "error_code": "BAD_REQUEST",
        "message": (
            "Upstream error: INVALID_ARGUMENT: The input (1100000 tokens) is "
            "longer than the model's context length (1048576 tokens)."
        ),
    }
    gzipped = _gzip.compress(json.dumps(upstream_error).encode())
    respx.post("https://dbc-test.cloud.databricks.com/serving-endpoints/chat/completions").mock(
        return_value=httpx.Response(
            400,
            content=gzipped,
            headers={"content-encoding": "gzip", "content-type": "application/json"},
        )
    )
    app = FastAPI()
    app.include_router(create_inference_proxy_router(store, enabled=True), prefix="/v1")  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="omnigent.server.routes.inference_proxy"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://server",
        ) as client:
            response = await client.post(
                (
                    f"/v1/runners/{runner_id}/sessions/conv_1/inference/completions/chat/completions"
                ),
                headers={RUNNER_TUNNEL_TOKEN_HEADER: binding_token},
                json={"model": "databricks-kimi-k3"},
            )

    assert response.status_code == 400
    assert response.headers.get("content-encoding") is None
    wrapped = response.json()
    assert wrapped["error"]["type"] == "upstream_error"
    assert "longer than the model's context length" in wrapped["error"]["message"]
    assert "longer than the model's context length" in caplog.text


@pytest.mark.asyncio
@respx.mock
async def test_proxy_passes_through_openai_shaped_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upstream body already carrying an ``error`` object is not re-wrapped."""
    binding_token = "runner-binding-secret"
    runner_id = token_bound_runner_id(binding_token)
    store = _ConversationStore(SimpleNamespace(runner_id=runner_id, host_id="host_1"))
    monkeypatch.setattr(
        inference_proxy,
        "default_provider_for_harness",
        lambda _config, _harness: SimpleNamespace(
            kind=inference_proxy.DATABRICKS_KIND,
            profile="local-profile",
        ),
    )
    monkeypatch.setattr(inference_proxy, "load_config", dict)
    monkeypatch.setattr(
        inference_proxy,
        "_profile_auth",
        lambda _profile, _workspace_origin: (
            _Auth(),
            "https://dbc-test.cloud.databricks.com",
        ),
    )
    monkeypatch.setattr(
        inference_proxy,
        "get_workspace_url_for_profile",
        lambda _profile: "https://dbc-test.cloud.databricks.com",
    )
    upstream_error = {
        "error": {
            "message": "The model's maximum context length is exceeded",
            "type": "invalid_request_error",
        }
    }
    respx.post("https://dbc-test.cloud.databricks.com/serving-endpoints/chat/completions").mock(
        return_value=httpx.Response(400, json=upstream_error)
    )
    app = FastAPI()
    app.include_router(create_inference_proxy_router(store, enabled=True), prefix="/v1")  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://server",
    ) as client:
        response = await client.post(
            (f"/v1/runners/{runner_id}/sessions/conv_1/inference/completions/chat/completions"),
            headers={RUNNER_TUNNEL_TOKEN_HEADER: binding_token},
            json={"model": "databricks-kimi-k3"},
        )

    assert response.status_code == 400
    assert response.json() == upstream_error


@pytest.mark.asyncio
@respx.mock
async def test_proxy_annotates_upstream_empty_error_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    binding_token = "runner-binding-secret"
    runner_id = token_bound_runner_id(binding_token)
    store = _ConversationStore(SimpleNamespace(runner_id=runner_id, host_id="host_1"))
    monkeypatch.setattr(
        inference_proxy,
        "default_provider_for_harness",
        lambda _config, _harness: SimpleNamespace(
            kind=inference_proxy.DATABRICKS_KIND,
            profile="local-profile",
        ),
    )
    monkeypatch.setattr(inference_proxy, "load_config", dict)
    monkeypatch.setattr(
        inference_proxy,
        "_profile_auth",
        lambda _profile, _workspace_origin: (
            _Auth(),
            "https://dbc-test.cloud.databricks.com",
        ),
    )
    monkeypatch.setattr(
        inference_proxy,
        "get_workspace_url_for_profile",
        lambda _profile: "https://dbc-test.cloud.databricks.com",
    )
    respx.post("https://dbc-test.cloud.databricks.com/serving-endpoints/chat/completions").mock(
        return_value=httpx.Response(400, content=b"")
    )
    app = FastAPI()
    app.include_router(create_inference_proxy_router(store, enabled=True), prefix="/v1")  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="omnigent.server.routes.inference_proxy"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://server",
        ) as client:
            response = await client.post(
                (
                    f"/v1/runners/{runner_id}/sessions/conv_1/inference/completions/chat/completions"
                ),
                headers={RUNNER_TUNNEL_TOKEN_HEADER: binding_token},
                json={"model": "databricks-glm-5-3-flash"},
            )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "upstream_error"
    assert "empty response body" in body["error"]["message"]
    assert "<empty>" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "model"),
    [
        ("completions/v1/chat/completions", "databricks-glm-5-2"),
        ("responses/responses", "system.ai.llama-4-maverick"),
    ],
)
async def test_proxy_rejects_near_match_path_and_unsupported_surface(
    path: str,
    model: str,
) -> None:
    binding_token = "runner-binding-secret"
    runner_id = token_bound_runner_id(binding_token)
    store = _ConversationStore(SimpleNamespace(runner_id=runner_id, host_id="host_1"))
    app = FastAPI()
    app.include_router(create_inference_proxy_router(store, enabled=True), prefix="/v1")  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://server",
    ) as client:
        response = await client.post(
            f"/v1/runners/{runner_id}/sessions/conv_1/inference/{path}",
            headers={RUNNER_TUNNEL_TOKEN_HEADER: binding_token},
            json={"model": model},
        )

    assert response.status_code in {400, 404}


@pytest.mark.asyncio
async def test_proxy_rejects_runner_not_bound_to_session() -> None:
    binding_token = "runner-binding-secret"
    runner_id = token_bound_runner_id(binding_token)
    store = _ConversationStore(SimpleNamespace(runner_id="runner_other", host_id="host_1"))
    app = FastAPI()
    app.include_router(create_inference_proxy_router(store, enabled=True), prefix="/v1")  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://server",
    ) as client:
        response = await client.post(
            f"/v1/runners/{runner_id}/sessions/conv_1/inference/responses/responses",
            headers={RUNNER_TUNNEL_TOKEN_HEADER: binding_token},
            json={},
        )

    assert response.status_code == 401
