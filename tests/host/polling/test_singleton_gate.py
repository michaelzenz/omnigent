"""Tests for singleton-plugin gating (bound-role host pin)."""

from __future__ import annotations

import httpx
import pytest

from omnigent.host.polling.singleton_gate import (
    RoleHostResolver,
    SingletonConfig,
    SingletonConfigError,
    parse_singleton_config,
    should_run_singleton,
)

# ── parse_singleton_config ──


def test_parse_singleton_enabled_with_role() -> None:
    cfg = parse_singleton_config({"singleton": True, "bound_role": "secretary"})
    assert cfg.singleton is True
    assert cfg.bound_role == "secretary"


def test_parse_singleton_false_does_not_require_bound_role() -> None:
    cfg = parse_singleton_config({"singleton": False})
    assert cfg.singleton is False
    assert cfg.bound_role is None


def test_parse_singleton_false_retains_inert_bound_role() -> None:
    cfg = parse_singleton_config({"singleton": False, "bound_role": "secretary"})
    assert cfg.singleton is False
    assert cfg.bound_role == "secretary"


def test_parse_singleton_missing_raises() -> None:
    """Omitting `singleton` entirely is a hard error (no silent default)."""
    with pytest.raises(SingletonConfigError):
        parse_singleton_config({})


def test_parse_singleton_non_dict_raises() -> None:
    with pytest.raises(SingletonConfigError):
        parse_singleton_config("not a dict")  # type: ignore[arg-type]


def test_parse_singleton_true_without_bound_role_raises() -> None:
    with pytest.raises(SingletonConfigError):
        parse_singleton_config({"singleton": True})


# ── RoleHostResolver ──


def _mock_client(status: int, body: dict | None) -> httpx.AsyncClient:
    transport = httpx.MockTransport(
        lambda req: (
            httpx.Response(status, json=body) if body is not None else httpx.Response(status)
        )
    )
    return httpx.AsyncClient(base_url="http://test", transport=transport)


@pytest.mark.asyncio
async def test_resolver_returns_host_id() -> None:
    client = _mock_client(200, {"host_id": "host-A"})
    resolver = RoleHostResolver(client, ttl_s=60.0)
    assert await resolver.get_role_host_id("secretary") == "host-A"


@pytest.mark.asyncio
async def test_resolver_caches_within_ttl() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"host_id": "host-A"})

    client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    resolver = RoleHostResolver(client, ttl_s=60.0)
    assert await resolver.get_role_host_id("secretary") == "host-A"
    assert await resolver.get_role_host_id("secretary") == "host-A"
    assert calls["n"] == 1  # cached


@pytest.mark.asyncio
async def test_resolver_refetches_after_ttl() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"host_id": "host-A"})

    client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    resolver = RoleHostResolver(client, ttl_s=0.0)  # expire immediately
    await resolver.get_role_host_id("secretary")
    await resolver.get_role_host_id("secretary")
    assert calls["n"] == 2  # not cached


@pytest.mark.asyncio
async def test_resolver_missing_host_id_returns_none() -> None:
    client = _mock_client(200, {"object": "agent.task.role_profile", "role": "x"})
    resolver = RoleHostResolver(client)
    assert await resolver.get_role_host_id("x") is None


@pytest.mark.asyncio
async def test_resolver_http_error_returns_none() -> None:
    client = _mock_client(404, None)
    resolver = RoleHostResolver(client)
    assert await resolver.get_role_host_id("x") is None


# ── should_run_singleton ──


@pytest.mark.asyncio
async def test_should_run_non_singleton_always_true() -> None:
    client = _mock_client(200, {"host_id": "other"})
    resolver = RoleHostResolver(client)
    cfg = SingletonConfig(singleton=False, bound_role=None)
    assert await should_run_singleton(resolver, cfg, host_id="host-A") is True


@pytest.mark.asyncio
async def test_should_run_singleton_when_host_matches() -> None:
    client = _mock_client(200, {"host_id": "host-A"})
    resolver = RoleHostResolver(client)
    cfg = SingletonConfig(singleton=True, bound_role="secretary")
    assert await should_run_singleton(resolver, cfg, host_id="host-A") is True


@pytest.mark.asyncio
async def test_should_run_singleton_when_host_differs() -> None:
    client = _mock_client(200, {"host_id": "host-A"})
    resolver = RoleHostResolver(client)
    cfg = SingletonConfig(singleton=True, bound_role="secretary")
    assert await should_run_singleton(resolver, cfg, host_id="host-B") is False


@pytest.mark.asyncio
async def test_should_run_singleton_missing_pin_skips() -> None:
    client = _mock_client(200, {"role": "secretary"})  # no host_id
    resolver = RoleHostResolver(client)
    cfg = SingletonConfig(singleton=True, bound_role="secretary")
    assert await should_run_singleton(resolver, cfg, host_id="host-A") is False


@pytest.mark.asyncio
async def test_should_run_singleton_fetch_error_skips() -> None:
    client = _mock_client(500, None)
    resolver = RoleHostResolver(client)
    cfg = SingletonConfig(singleton=True, bound_role="secretary")
    assert await should_run_singleton(resolver, cfg, host_id="host-A") is False
