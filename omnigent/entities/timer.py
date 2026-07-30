"""Deferred timer items executed by a registered host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TimerItem:
    """
    One deferred action scheduled for a specific host.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param task_type: Handler key (e.g. ``"prompt"``).
    :param fire_at: Unix epoch seconds when the item becomes due.
    :param state: ``pending``, ``running``, ``done``, or ``failed``.
    :param host_id: Host that must claim and execute this item.
    :param payload: Handler-specific JSON object.
    :param owner_user_id: Creating user, when set.
    :param created_at: Unix epoch seconds at row creation.
    :param fired_at: Unix epoch seconds when execution started, or ``None``.
    """

    id: str
    task_type: str
    fire_at: int
    state: str
    host_id: str
    payload: dict[str, Any]
    created_at: int
    owner_user_id: str | None = None
    fired_at: int | None = None
