"""Focused tests for the monthly Omnigent usage ledger and API."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnigent.db.db_models import workspace_scope
from omnigent.errors import OmnigentError
from omnigent.llms.context_window import ModelPricing
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.routes._sessions.orchestration import (
    _accumulate_session_usage,
    _pending_omniharness_workloads,
)
from omnigent.server.routes.statistics import create_statistics_router
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.usage_ledger import record_omniharness_usage


class _HeaderAuth:
    def get_user_id(self, request: Request) -> str | None:
        return request.headers.get("x-test-user")


class _ModelSettings:
    def __init__(self, models: list[str]) -> None:
        self._settings = SimpleNamespace(
            harness_models={"omniharness": models},
            workload_classification_enabled=True,
        )

    def get(self) -> SimpleNamespace:
        return self._settings


def _statistics_client(
    store: SqlAlchemyConversationStore,
    models: list[str] | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_statistics_router(
            store,
            model_settings_store=_ModelSettings(models or []),  # type: ignore[arg-type]
            auth_provider=_HeaderAuth(),  # type: ignore[arg-type]
        ),
        prefix="/v1",
    )

    @app.exception_handler(OmnigentError)
    async def handle_error(_request: Request, exc: OmnigentError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": exc.code})

    return TestClient(app)


def _record(
    store: SqlAlchemyConversationStore,
    *,
    user: str,
    occurred_at: int,
    model: str = "model-a",
    purpose: str = "user_interaction",
    workload: str | None = "development",
    priced: bool = True,
    cost: float | None = 0.01,
) -> None:
    store.record_usage_ledger(
        {
            "user_id": user,
            "occurred_at": occurred_at,
            "purpose": purpose,
            "model": model,
            "workload": workload,
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 5,
            "input_price_per_token": 0.000001 if priced else None,
            "output_price_per_token": 0.000002 if priced else None,
            "cache_read_price_per_token": 0.0000001 if priced else None,
            "cache_write_price_per_token": 0.00000125 if priced else None,
            "cost_usd": cost,
            "priced": priced,
        }
    )


def test_ledger_month_scope_and_historical_price_snapshot(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    _record(store, user="alice", occurred_at=1_775_347_200)  # 2026-04-01 UTC
    _record(
        store,
        user="alice",
        occurred_at=1_777_939_200,  # 2026-05-01 UTC
        priced=False,
        cost=None,
    )
    _record(store, user="bob", occurred_at=1_775_347_200)

    april = store.list_usage_ledger_month("alice", "2026-04")
    assert len(april) == 1
    assert april[0]["input_price_per_token"] == 0.000001
    assert april[0]["cost_usd"] == 0.01
    assert store.list_usage_ledger_months("alice") == ["2026-05", "2026-04"]


def test_statistics_api_aggregates_tokens_and_marks_unpriced(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    _record(store, user="alice", occurred_at=1_775_347_200)
    _record(
        store,
        user="alice",
        occurred_at=1_775_433_600,
        model="model-b",
        purpose="smart_routing+workload_classification",
        workload="debug",
        priced=False,
        cost=None,
    )
    _record(store, user="bob", occurred_at=1_775_347_200, cost=99.0)

    app = FastAPI()
    app.include_router(
        create_statistics_router(store, auth_provider=_HeaderAuth()),  # type: ignore[arg-type]
        prefix="/v1",
    )
    response = TestClient(app).get(
        "/v1/statistics?month=2026-04",
        headers={"x-test-user": "alice"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["month"] == "2026-04"
    assert payload["totals"] == {
        "key": "total",
        "calls": 2,
        "priced_calls": 1,
        "unpriced_calls": 1,
        "input_tokens": 200,
        "output_tokens": 40,
        "cache_read_input_tokens": 60,
        "cache_creation_input_tokens": 10,
        "total_tokens": 310,
        "cost_usd": 0.01,
    }
    assert {item["key"] for item in payload["by_purpose"]} == {
        "user_interaction",
        "smart_routing+workload_classification",
    }
    assert payload["workload_classification_enabled"] is False


def test_statistics_api_requires_authenticated_user(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    app = FastAPI()
    app.include_router(
        create_statistics_router(store, auth_provider=_HeaderAuth()),  # type: ignore[arg-type]
        prefix="/v1",
    )

    @app.exception_handler(OmnigentError)
    async def handle_error(_request: Request, exc: OmnigentError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": exc.code})

    response = TestClient(app).get("/v1/statistics?month=2026-04")
    assert response.status_code == 401
    assert (
        TestClient(app)
        .put(
            "/v1/statistics/model-pricing/model-a",
            json={"input_price_per_million": 1, "output_price_per_million": 2},
        )
        .status_code
        == 401
    )
    assert (
        TestClient(app)
        .delete(
            "/v1/statistics/model-pricing/model-a",
        )
        .status_code
        == 401
    )


def test_omnigent_completion_writes_one_ledger_row(
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="ledger")
    store.update_conversation(conv.id, harness_override="openai-agents")
    pricing = ModelPricing(input_per_token=0.001, output_per_token=0.002)
    metadata = SimpleNamespace(pricing=pricing, pricing_source="mlflow")
    monkeypatch.setattr(
        "omnigent.omniharness_model_catalog.find_omniharness_model_metadata",
        lambda _model: metadata,
    )
    monkeypatch.setattr(
        "omnigent.usage_ledger.get_omniharness_model_metadata",
        lambda _model: metadata,
    )
    _pending_omniharness_workloads[conv.id] = ("turn_abc", "code_review")

    _accumulate_session_usage(
        {
            "usage": {
                "model": "model-a",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            }
        },
        conv.id,
        store,
    )

    month = store.list_usage_ledger_months(RESERVED_USER_LOCAL)[0]
    rows = store.list_usage_ledger_month(RESERVED_USER_LOCAL, month)
    assert len(rows) == 1
    assert rows[0]["purpose"] == "user_interaction"
    assert rows[0]["turn_id"] == "turn_abc"
    assert rows[0]["workload"] == "code_review"
    assert rows[0]["cost_usd"] == 0.02
    assert store.get_conversation(conv.id).session_usage["total_tokens"] == 15


def test_model_pricing_crud_is_user_scoped_and_reset_restores_service(
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    service = ModelPricing(
        input_per_token=0.000001,
        output_per_token=0.000003,
        cache_read_per_token=0.0000001,
        cache_write_per_token=0.00000125,
    )
    monkeypatch.setattr(
        "omnigent.server.routes.statistics.get_omniharness_model_metadata",
        lambda _model: SimpleNamespace(pricing=service, pricing_source="mlflow"),
    )
    client = _statistics_client(store, ["model-a"])

    saved = client.put(
        "/v1/statistics/model-pricing/model-a",
        headers={"x-test-user": "alice"},
        json={
            "input_price_per_million": 2,
            "output_price_per_million": 4,
            "cache_read_price_per_million": 0.25,
            "cache_write_price_per_million": 2.5,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["input_price_per_million"] == 2

    alice = client.get("/v1/statistics?month=2026-04", headers={"x-test-user": "alice"}).json()
    row = alice["current_pricing"][0]
    assert row["service_input_price_per_token"] == 0.000001
    assert row["custom_input_price_per_token"] == 0.000002
    assert row["effective_input_price_per_token"] == 0.000002
    assert row["has_custom_pricing"] is True
    assert row["custom_differs_from_service"] is True
    assert row["service_pricing_status"] == "known"

    bob = client.get("/v1/statistics?month=2026-04", headers={"x-test-user": "bob"}).json()
    assert bob["current_pricing"][0]["has_custom_pricing"] is False
    assert (
        client.delete(
            "/v1/statistics/model-pricing/model-a",
            headers={"x-test-user": "bob"},
        ).status_code
        == 204
    )
    assert store.get_model_pricing_override("alice", "model-a") is not None

    assert (
        client.delete(
            "/v1/statistics/model-pricing/model-a",
            headers={"x-test-user": "alice"},
        ).status_code
        == 204
    )
    reset = client.get("/v1/statistics?month=2026-04", headers={"x-test-user": "alice"}).json()
    reset_row = reset["current_pricing"][0]
    assert reset_row["has_custom_pricing"] is False
    assert reset_row["effective_input_price_per_token"] == 0.000001


def test_model_pricing_override_is_workspace_scoped(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    pricing = {
        "input_price_per_token": 0.000001,
        "output_price_per_token": 0.000002,
        "cache_read_price_per_token": None,
        "cache_write_price_per_token": None,
    }
    with workspace_scope(101):
        store.set_model_pricing_override("alice", "model-a", pricing)
        assert store.get_model_pricing_override("alice", "model-a") is not None
    with workspace_scope(202):
        assert store.get_model_pricing_override("alice", "model-a") is None


def test_equal_custom_pricing_is_tolerance_safe(
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    service = ModelPricing(input_per_token=0.000001, output_per_token=0.000003)
    monkeypatch.setattr(
        "omnigent.server.routes.statistics.get_omniharness_model_metadata",
        lambda _model: SimpleNamespace(pricing=service, pricing_source="mlflow"),
    )
    client = _statistics_client(store, ["model-a"])
    response = client.put(
        "/v1/statistics/model-pricing/model-a",
        headers={"x-test-user": "alice"},
        json={
            "input_price_per_million": 1.0000000001,
            "output_price_per_million": 3.0000000001,
        },
    )
    assert response.status_code == 200
    report = client.get("/v1/statistics?month=2026-04", headers={"x-test-user": "alice"}).json()
    assert report["current_pricing"][0]["custom_differs_from_service"] is False


def test_unknown_service_remains_unknown_with_custom_pricing(
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    monkeypatch.setattr(
        "omnigent.server.routes.statistics.get_omniharness_model_metadata",
        lambda _model: SimpleNamespace(pricing=None, pricing_source=None),
    )
    client = _statistics_client(store, ["unknown-model"])
    assert (
        client.put(
            "/v1/statistics/model-pricing/unknown-model",
            headers={"x-test-user": "alice"},
            json={"input_price_per_million": 2, "output_price_per_million": 5},
        ).status_code
        == 200
    )

    report = client.get("/v1/statistics?month=2026-04", headers={"x-test-user": "alice"}).json()
    row = report["current_pricing"][0]
    assert row["service_pricing_status"] == "unknown"
    assert row["pricing_status"] == "known"
    assert row["service_input_price_per_token"] is None
    assert row["effective_cache_read_price_per_token"] == pytest.approx(0.0000002)
    assert row["effective_cache_write_price_per_token"] == pytest.approx(0.0000025)
    assert row["custom_differs_from_service"] is False

    client.delete(
        "/v1/statistics/model-pricing/unknown-model",
        headers={"x-test-user": "alice"},
    )
    reset = client.get("/v1/statistics?month=2026-04", headers={"x-test-user": "alice"}).json()
    assert reset["current_pricing"][0]["pricing_status"] == "unknown"
    assert reset["current_pricing"][0]["effective_input_price_per_token"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"input_price_per_million": -1, "output_price_per_million": 2},
        {"input_price_per_million": "bad", "output_price_per_million": 2},
        {"output_price_per_million": 2},
    ],
)
def test_model_pricing_validation_rejects_malformed_values(
    db_uri: str,
    body: dict[str, object],
) -> None:
    client = _statistics_client(SqlAlchemyConversationStore(db_uri))
    response = client.put(
        "/v1/statistics/model-pricing/model-a",
        headers={"x-test-user": "alice"},
        json=body,
    )
    assert response.status_code in (400, 422)


def test_model_pricing_validation_rejects_nonfinite_values(db_uri: str) -> None:
    client = _statistics_client(SqlAlchemyConversationStore(db_uri))
    response = client.put(
        "/v1/statistics/model-pricing/model-a",
        headers={"x-test-user": "alice", "content-type": "application/json"},
        content='{"input_price_per_million": NaN, "output_price_per_million": 2}',
    )
    assert response.status_code == 400


def test_future_ledger_rows_use_custom_pricing_without_changing_history(
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    _record(store, user="alice", occurred_at=1_775_347_200, cost=0.01)
    historical = store.list_usage_ledger_month("alice", "2026-04")[0].copy()
    store.set_model_pricing_override(
        "alice",
        "model-a",
        {
            "input_price_per_token": 0.000002,
            "output_price_per_token": 0.000004,
            "cache_read_price_per_token": 0.0000003,
            "cache_write_price_per_token": 0.000003,
        },
    )
    monkeypatch.setattr(store, "get_session_owner", lambda _session_id: "alice")
    monkeypatch.setattr(
        "omnigent.usage_ledger.get_omniharness_model_metadata",
        lambda _model: SimpleNamespace(
            pricing=ModelPricing(input_per_token=0.000001, output_per_token=0.000003),
            pricing_source="mlflow",
        ),
    )

    record_omniharness_usage(
        store,
        session_id="1" * 32,
        turn_id="turn_new",
        purpose="user_interaction",
        model="model-a",
        workload="development",
        usage={"input_tokens": 10, "output_tokens": 5},
    )

    assert store.list_usage_ledger_month("alice", "2026-04")[0] == historical
    current_month = store.list_usage_ledger_months("alice")[0]
    new_row = next(
        row
        for row in store.list_usage_ledger_month("alice", current_month)
        if row["turn_id"] == "turn_new"
    )
    assert new_row["pricing_source"] == "custom"
    assert new_row["input_price_per_token"] == 0.000002
    assert new_row["cost_usd"] == pytest.approx(0.00004)
