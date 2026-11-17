"""Embeddings for the GlobalHub board search.

The server embeds the title and the overview of each task with one model, and
stores unit vectors, so the similarity of two vectors is their dot product.
Every vector has :data:`EMBEDDING_DIMENSIONS` values, as the GTE and BGE
endpoints return. A task embeds at most :data:`MAX_EMBEDDED_CHARS` characters:
a token holds at least one character, so the text always fits the input of
8,192 tokens of the GTE endpoint. The settings live under ``globalhub.search``
in the server config; without an endpoint, the board search uses keywords only.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import sys
import threading
from array import array
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

EMBEDDING_DIMENSIONS = 1024
DEFAULT_MIN_SCORE = 0.5
MAX_EMBEDDED_CHARS = 8_000
_REQUEST_TIMEOUT_S = 30.0

Credentials = Callable[[], tuple[str, Mapping[str, str]]]


class EmbeddingError(Exception):
    """An embedding call failed, or it returned a vector that cannot be used."""


@dataclass(frozen=True)
class TaskEmbeddingSettings:
    """The ``globalhub.search`` settings of the server.

    :param endpoint: The Databricks serving endpoint that embeds text, e.g.
        ``"databricks-gte-large-en"``.
    :param databricks_profile: The Databricks CLI profile, or ``None`` for the
        default credentials, e.g. the service principal of a Databricks App.
    :param min_score: The least similarity that makes a task, a title or a
        worker match the query.
    """

    endpoint: str
    databricks_profile: str | None
    min_score: float


def load_task_embedding_settings(config: Mapping[str, object]) -> TaskEmbeddingSettings | None:
    """Read ``globalhub.search`` from the server config, or return ``None`` without an endpoint."""
    globalhub = config.get("globalhub")
    search = globalhub.get("search") if isinstance(globalhub, Mapping) else None
    if not isinstance(search, Mapping):
        return None
    endpoint = search.get("embedding_endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        return None
    profile = search.get("databricks_profile")
    min_score = search.get("min_score", DEFAULT_MIN_SCORE)
    return TaskEmbeddingSettings(
        endpoint=endpoint.strip(),
        databricks_profile=profile.strip()
        if isinstance(profile, str) and profile.strip()
        else None,
        min_score=(
            float(min_score)
            if isinstance(min_score, int | float) and not isinstance(min_score, bool)
            else DEFAULT_MIN_SCORE
        ),
    )


class Embedder(Protocol):
    """Turns texts into unit vectors with one model."""

    model: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one unit vector of :data:`EMBEDDING_DIMENSIONS` values per text, in order."""
        ...


class DatabricksEmbedder:
    """Embeds texts with a Databricks serving endpoint, as the identity of the server."""

    def __init__(
        self,
        endpoint: str,
        *,
        databricks_profile: str | None = None,
        credentials: Credentials | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        :param endpoint: The serving endpoint, which also names the model.
        :param databricks_profile: The Databricks CLI profile, or ``None`` for the
            default credentials of the Databricks SDK.
        :param credentials: Returns the workspace URL and the auth headers; the
            default reads them from the Databricks SDK.
        :param transport: The HTTP transport, for tests.
        """
        self.model = endpoint
        self._profile = databricks_profile
        self._credentials = credentials or self._sdk_credentials
        self._transport = transport
        self._config: Any = None
        self._config_lock = threading.Lock()

    def _sdk_credentials(self) -> tuple[str, Mapping[str, str]]:
        from databricks.sdk.core import Config

        with self._config_lock:
            if self._config is None:
                self._config = Config(profile=self._profile) if self._profile else Config()
            config = self._config
        return str(config.host), dict(config.authenticate())

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one unit vector per text; raise :class:`EmbeddingError` when the call fails."""
        if not texts:
            return []
        try:
            host, headers = await asyncio.to_thread(self._credentials)
        except Exception as exc:
            raise EmbeddingError(
                f"no Databricks credentials for {self.model} ({type(exc).__name__})"
            ) from exc
        url = f"{host.rstrip('/')}/serving-endpoints/{self.model}/invocations"
        try:
            async with httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT_S, transport=self._transport
            ) as client:
                response = await client.post(url, json={"input": list(texts)}, headers=headers)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise EmbeddingError(
                f"the {self.model} endpoint is not reachable ({type(exc).__name__})"
            ) from exc
        if response.status_code != 200:
            raise EmbeddingError(f"the {self.model} endpoint answered {response.status_code}")
        try:
            items = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [unit_vector(item["embedding"]) for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"the {self.model} endpoint sent an unusable answer") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(f"the {self.model} endpoint sent {len(vectors)} vectors")
        return vectors


def unit_vector(values: Sequence[float]) -> list[float]:
    """Return ``values`` scaled to length 1.

    :raises ValueError: When the vector has another length than
        :data:`EMBEDDING_DIMENSIONS`, holds a value that is not finite, or is zero.
    """
    floats = [float(value) for value in values]
    if len(floats) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"expected {EMBEDDING_DIMENSIONS} values, got {len(floats)}")
    norm = math.sqrt(math.sumprod(floats, floats))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("the vector has no direction")
    return [value / norm for value in floats]


def task_embedding_text(title: str, description: str | None) -> str:
    """Return the text that a task embeds: its title, then its overview."""
    text = title.strip()
    if description and description.strip():
        text = f"{text}\n\n{description.strip()}"
    return text[:MAX_EMBEDDED_CHARS]


def text_sha256(text: str) -> str:
    """Return the SHA-256 hex digest of ``text``, which shows whether a task's text changed."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vector_to_bytes(vector: Sequence[float]) -> bytes:
    """Return the vector as little-endian float32 values."""
    values = array("f", vector)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def vector_from_bytes(blob: bytes) -> array[float]:
    """Read a vector that :func:`vector_to_bytes` wrote."""
    values = array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def similarity(first: Sequence[float], second: Sequence[float]) -> float:
    """Return the cosine similarity of two unit vectors."""
    return math.sumprod(first, second)
