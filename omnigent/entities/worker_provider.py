"""Reusable definitions for initializing PuppyGarden workers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkerProvider:
    """A prompt-free definition of how to initialize a Worker."""

    id: str
    name: str
    kind: str
    configuration: str
    created_at: int
    description: str | None = None
    built_in: bool = False
    updated_at: int | None = None
