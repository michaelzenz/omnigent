"""Tests for the embedding settings, the Databricks embedder and the vector helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
import pytest

from omnigent.agent_tasks.embeddings import (
    DEFAULT_MIN_SCORE,
    EMBEDDING_DIMENSIONS,
    MAX_EMBEDDED_CHARS,
    DatabricksEmbedder,
    EmbeddingError,
    TaskEmbeddingSettings,
    load_task_embedding_settings,
    task_embedding_text,
    unit_vector,
    vector_from_bytes,
    vector_to_bytes,
)

ENDPOINT = "databricks-gte-large-en"
WORKSPACE = "https://workspace.example.com/"
HEADERS = {"X-Test-Credential": "example"}


def _values(*leading: float) -> list[float]:
    return [*leading, *[0.0] * (EMBEDDING_DIMENSIONS - len(leading))]


def _credentials() -> tuple[str, Mapping[str, str]]:
    return WORKSPACE, HEADERS


def _search(**settings: object) -> dict[str, object]:
    return {"globalhub": {"search": settings}}


def test_the_settings_need_an_endpoint_and_otherwise_use_the_defaults() -> None:
    assert load_task_embedding_settings({}) is None
    assert load_task_embedding_settings(_search(embedding_endpoint=" ")) is None
    assert load_task_embedding_settings(
        _search(embedding_endpoint=ENDPOINT, min_score=True)
    ) == TaskEmbeddingSettings(
        endpoint=ENDPOINT, databricks_profile=None, min_score=DEFAULT_MIN_SCORE
    )
    assert load_task_embedding_settings(
        _search(embedding_endpoint=ENDPOINT, databricks_profile="dev", min_score=0.6)
    ) == TaskEmbeddingSettings(endpoint=ENDPOINT, databricks_profile="dev", min_score=0.6)


async def test_the_databricks_embedder_posts_the_texts_and_returns_unit_vectors_in_order() -> None:
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        items = [
            {"index": 1, "embedding": _values(0.0, 2.0)},
            {"index": 0, "embedding": _values(3.0, 4.0)},
        ]
        return httpx.Response(200, json={"data": items})

    embedder = DatabricksEmbedder(
        ENDPOINT, credentials=_credentials, transport=httpx.MockTransport(answer)
    )

    vectors = await embedder.embed(["first", "second"])

    assert [vector[:2] for vector in vectors] == [[0.6, 0.8], [0.0, 1.0]]
    assert str(requests[0].url) == (
        f"https://workspace.example.com/serving-endpoints/{ENDPOINT}/invocations"
    )
    assert requests[0].headers["X-Test-Credential"] == "example"
    assert json.loads(requests[0].content) == {"input": ["first", "second"]}
    assert await embedder.embed([]) == []
    assert len(requests) == 1


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(429, json={"error_code": "REQUEST_LIMIT_EXCEEDED"}),
        httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}),
        httpx.Response(200, json={"data": [{"index": 0, "embedding": _values()}]}),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, text="not json"),
    ],
    ids=["refused", "short-vector", "zero-vector", "missing-vector", "not-json"],
)
async def test_the_databricks_embedder_raises_for_an_unusable_answer(
    answer: httpx.Response,
) -> None:
    embedder = DatabricksEmbedder(
        ENDPOINT, credentials=_credentials, transport=httpx.MockTransport(lambda request: answer)
    )

    with pytest.raises(EmbeddingError):
        await embedder.embed(["text"])


async def test_the_databricks_embedder_raises_without_credentials() -> None:
    def no_credentials() -> tuple[str, Mapping[str, str]]:
        raise ValueError("no profile")

    with pytest.raises(EmbeddingError):
        await DatabricksEmbedder(ENDPOINT, credentials=no_credentials).embed(["text"])


def test_a_vector_keeps_its_values_as_bytes_and_a_task_embeds_its_title_and_overview() -> None:
    vector = unit_vector(_values(3.0, 4.0))
    blob = vector_to_bytes(vector)

    assert len(blob) == 4 * EMBEDDING_DIMENSIONS
    assert list(vector_from_bytes(blob))[:3] == pytest.approx([0.6, 0.8, 0.0])
    assert task_embedding_text(" Fix the retries ", " S3 uploads time out ") == (
        "Fix the retries\n\nS3 uploads time out"
    )
    assert task_embedding_text("Fix the retries", " ") == "Fix the retries"
    assert len(task_embedding_text("x" * (MAX_EMBEDDED_CHARS + 10), None)) == MAX_EMBEDDED_CHARS
