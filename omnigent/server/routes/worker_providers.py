"""Worker Provider CRUD for PuppyGarden."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.worker_provider_store import WorkerProviderStore

_DEFAULT_PROVIDER_NAME = "Default Worker"
_DEFAULT_PROVIDER_ID = uuid.uuid5(uuid.NAMESPACE_URL, "omnigent:worker-provider:default").hex
_REQUIRED_CAPABILITIES = [
    "initialize",
    "multi_turn",
    "streaming",
    "interrupt",
    "terminate",
    "observe_response_request",
    "resume",
]


class WorkerProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    kind: Literal["internal", "external"] = "internal"
    configuration: dict[str, Any] = Field(default_factory=dict)


class WorkerProviderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    configuration: dict[str, Any] | None = None


def _decode_configuration(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _internal_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    return {
        key: configuration.get(key)
        for key in ("agent_id", "model")
        if configuration.get(key) is not None
    }


def _provider_response(
    provider: Any,
    agent_store: AgentStore,
) -> dict[str, Any]:
    configuration = _decode_configuration(provider.configuration)
    if provider.kind == "internal":
        configuration = _internal_configuration(configuration)
    available = True
    unavailable_reason: str | None = None
    if provider.kind == "external":
        available = configuration.get("available") is True
        if not available:
            unavailable_reason = str(
                configuration.get("unavailable_reason") or "External adapter is unavailable"
            )
    if provider.kind == "internal":
        agent_id = configuration.get("agent_id")
        agent = agent_store.get(agent_id) if isinstance(agent_id, str) and agent_id else None
        if agent is None:
            available = False
            unavailable_reason = "Select an available execution target"
        elif not agent.enabled or agent.archived:
            available = False
            unavailable_reason = "The selected execution target is unavailable"
    return {
        "id": provider.id,
        "object": "worker_provider",
        "name": provider.name,
        "description": provider.description,
        "kind": provider.kind,
        "configuration": configuration,
        "built_in": provider.built_in,
        "available": available,
        "unavailable_reason": unavailable_reason,
        "capabilities": _REQUIRED_CAPABILITIES,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


def ensure_default_worker_provider(
    store: WorkerProviderStore,
    agent_store: AgentStore | None = None,
) -> None:
    """Seed the protected internal provider once per workspace."""
    if store.get(_DEFAULT_PROVIDER_ID) is not None:
        return
    from omnigent.execution_targets import ONIH_OPENAI_AGENTS_TARGET

    omni_harness = (
        agent_store.get_by_name(ONIH_OPENAI_AGENTS_TARGET) if agent_store is not None else None
    )
    configuration = {"agent_id": omni_harness.id} if omni_harness is not None else {}
    store.create(
        _DEFAULT_PROVIDER_ID,
        _DEFAULT_PROVIDER_NAME,
        "internal",
        json.dumps(configuration, sort_keys=True),
        description="Creates an internal worker using a selected harness and model.",
        built_in=True,
    )


def create_worker_providers_router(
    store: WorkerProviderStore,
    agent_store: AgentStore,
    *,
    auth_provider: AuthProvider | None = None,
    host_store: HostStore | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/worker-providers")
    async def list_worker_providers(request: Request) -> dict[str, Any]:
        require_user(request, auth_provider)
        ensure_default_worker_provider(store, agent_store)
        return {
            "object": "list",
            "data": [_provider_response(provider, agent_store) for provider in store.list()],
        }

    @router.get("/worker-providers/{provider_id}")
    async def get_worker_provider(
        request: Request,
        provider_id: str,
    ) -> dict[str, Any]:
        require_user(request, auth_provider)
        provider = store.get(provider_id)
        if provider is None:
            raise OmnigentError("Worker provider not found", code=ErrorCode.NOT_FOUND)
        return _provider_response(provider, agent_store)

    @router.post("/worker-providers", status_code=201)
    async def create_worker_provider(
        request: Request,
        body: WorkerProviderCreateRequest,
    ) -> dict[str, Any]:
        require_user(request, auth_provider)
        if body.kind == "external":
            raise OmnigentError(
                "External providers must be registered by an adapter",
                code=ErrorCode.INVALID_INPUT,
            )
        provider = store.create(
            uuid.uuid4().hex,
            body.name.strip(),
            body.kind,
            json.dumps(_internal_configuration(body.configuration), sort_keys=True),
            description=body.description,
        )
        return _provider_response(provider, agent_store)

    @router.patch("/worker-providers/{provider_id}")
    async def update_worker_provider(
        request: Request,
        provider_id: str,
        body: WorkerProviderUpdateRequest,
    ) -> dict[str, Any]:
        require_user(request, auth_provider)
        existing = store.get(provider_id)
        if existing is None:
            raise OmnigentError("Worker provider not found", code=ErrorCode.NOT_FOUND)
        fields: dict[str, Any] = {}
        if "name" in body.model_fields_set:
            fields["name"] = body.name.strip() if body.name else body.name
        if "description" in body.model_fields_set:
            fields["description"] = body.description
        if "configuration" in body.model_fields_set:
            configuration = body.configuration or {}
            if existing.kind == "internal":
                configuration = _internal_configuration(configuration)
            fields["configuration"] = json.dumps(configuration, sort_keys=True)
        provider = store.update(provider_id, **fields)
        assert provider is not None
        return _provider_response(provider, agent_store)

    @router.delete("/worker-providers/{provider_id}", status_code=204)
    async def delete_worker_provider(request: Request, provider_id: str) -> Response:
        require_user(request, auth_provider)
        if not store.delete(provider_id):
            raise OmnigentError("Worker provider not found", code=ErrorCode.NOT_FOUND)
        return Response(status_code=204)

    return router
