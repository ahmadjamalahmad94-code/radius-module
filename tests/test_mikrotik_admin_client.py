"""Tests for K2 — admin-facing MikroTik client wrapper.

We don't talk to a real router here. Tests:
- Cache TTL semantics (one network call, two reads).
- Error envelope (auth/connect/trap mapped to MtResult.ok=False).
- Failed reads are NOT cached.
- `resolve_connection_address` is consulted for `host`.
- VPN-mode rows dial the peer IP, direct-mode rows dial the public.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.radius.services import mikrotik_admin_client as mac
from app.radius.services.mikrotik_admin_client import (
    MtResult,
    fetch_cached,
    invalidate_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    mac._cache.clear()
    yield
    mac._cache.clear()


@pytest.fixture
def fake_nas_direct():
    return {
        "id": 1,
        "address": "203.0.113.5",
        "connection_mode": "direct",
        "api_port": 8728,
        "api_user": "admin",
        "api_password": "x",
        "api_use_tls": 0,
    }


@pytest.fixture
def fake_nas_vpn():
    return {
        "id": 2,
        "address": "203.0.113.6",
        "connection_mode": "vpn",
        "vpn_peer_address": "10.10.0.5",
        "api_port": 8728,
        "api_user": "admin",
        "api_password": "x",
    }


def _patched_pool(client_mock):
    """Yields a fake `_pool_acquire(cfg)` ctxmanager that returns
    the given mock client."""
    from contextlib import contextmanager

    @contextmanager
    def fake_acquire(cfg):
        # Store the cfg so the test can assert on the dialled host.
        fake_acquire.last_cfg = cfg
        yield client_mock

    return fake_acquire


def test_direct_mode_dials_public_address(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{"cpu-load": "12"}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = fetch_cached(
            nas=fake_nas_direct,
            operation="op1",
            ttl_sec=60,
            work=lambda c: list(c.print_("/system/resource/print")),
        )

    assert res.ok is True
    assert res.dialed_address == "203.0.113.5"
    assert res.mode == "direct"
    assert res.data == [{"cpu-load": "12"}]
    assert fake.last_cfg["host"] == "203.0.113.5"


def test_vpn_mode_dials_peer_ip(fake_nas_vpn):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{"cpu-load": "8"}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = fetch_cached(
            nas=fake_nas_vpn,
            operation="op1",
            ttl_sec=60,
            work=lambda c: list(c.print_("/system/resource/print")),
        )

    assert res.ok is True
    assert res.dialed_address == "10.10.0.5"
    assert res.mode == "vpn"
    assert fake.last_cfg["host"] == "10.10.0.5"


def test_cache_returns_same_value_within_ttl(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{"v": 1}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        a = fetch_cached(
            nas=fake_nas_direct, operation="op-cached",
            ttl_sec=60, work=lambda c: list(c.print_("/")),
        )
        b = fetch_cached(
            nas=fake_nas_direct, operation="op-cached",
            ttl_sec=60, work=lambda c: list(c.print_("/")),
        )

    assert a.ok and b.ok
    assert a.cached is False
    assert b.cached is True
    # Only one underlying network call.
    assert mock_client.print_.call_count == 1


def test_cache_expires_after_ttl(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{"v": 1}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        a = fetch_cached(
            nas=fake_nas_direct, operation="op-ttl",
            ttl_sec=0.05, work=lambda c: list(c.print_("/")),
        )
        time.sleep(0.1)
        b = fetch_cached(
            nas=fake_nas_direct, operation="op-ttl",
            ttl_sec=0.05, work=lambda c: list(c.print_("/")),
        )

    assert a.cached is False
    assert b.cached is False
    assert mock_client.print_.call_count == 2


def test_auth_error_returns_friendly_envelope(fake_nas_direct):
    from app.radius.integration.mikrotik.errors import AuthError
    from contextlib import contextmanager

    @contextmanager
    def fake_acquire(cfg):
        raise AuthError("bad password")
        yield  # never reached  # pragma: no cover

    with patch.object(mac, "_pool_acquire", fake_acquire):
        res = fetch_cached(
            nas=fake_nas_direct, operation="op-auth",
            ttl_sec=60, work=lambda c: c,
        )

    assert res.ok is False
    assert "تسجيل الدخول" in res.error
    assert res.dialed_address == "203.0.113.5"


def test_connect_error_returns_friendly_envelope(fake_nas_direct):
    from app.radius.integration.mikrotik.errors import ConnectError
    from contextlib import contextmanager

    @contextmanager
    def fake_acquire(cfg):
        raise ConnectError("connection refused")
        yield  # pragma: no cover

    with patch.object(mac, "_pool_acquire", fake_acquire):
        res = fetch_cached(
            nas=fake_nas_direct, operation="op-conn",
            ttl_sec=60, work=lambda c: c,
        )

    assert res.ok is False
    assert "تعذر الاتصال" in res.error


def test_failed_reads_are_NOT_cached(fake_nas_direct):
    """An error must not poison the cache — we want the UI to
    recover the moment the router comes back."""
    from app.radius.integration.mikrotik.errors import ConnectError
    from contextlib import contextmanager

    calls = []

    @contextmanager
    def fake_acquire(cfg):
        calls.append(1)
        raise ConnectError("nope")
        yield  # pragma: no cover

    with patch.object(mac, "_pool_acquire", fake_acquire):
        a = fetch_cached(nas=fake_nas_direct, operation="op-fail",
                         ttl_sec=60, work=lambda c: c)
        b = fetch_cached(nas=fake_nas_direct, operation="op-fail",
                         ttl_sec=60, work=lambda c: c)

    assert a.ok is False and b.ok is False
    # Both calls actually hit the (failing) network — no cached error.
    assert len(calls) == 2


def test_invalidate_cache_drops_entry(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{"v": 1}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        fetch_cached(nas=fake_nas_direct, operation="op-inv",
                     ttl_sec=60, work=lambda c: list(c.print_("/")))
        invalidate_cache(int(fake_nas_direct["id"]), "op-inv")
        fetch_cached(nas=fake_nas_direct, operation="op-inv",
                     ttl_sec=60, work=lambda c: list(c.print_("/")))

    # Cache invalidated → second call must hit the wire.
    assert mock_client.print_.call_count == 2


def test_empty_address_short_circuits():
    """A NAS with no address (admin half-filled the row) returns a
    clean error without trying to dial anything."""
    nas = {"id": 99, "connection_mode": "direct"}
    res = fetch_cached(
        nas=nas, operation="op-empty",
        ttl_sec=60, work=lambda c: c,
    )
    assert res.ok is False
    assert "غير محدد" in res.error
    assert res.dialed_address == ""


def test_mt_result_to_dict_round_trips():
    r = MtResult(
        ok=True, data={"x": 1}, took_ms=42,
        cached=False, dialed_address="10.0.0.1", mode="vpn",
    )
    d = r.to_dict()
    assert d["ok"] is True
    assert d["data"] == {"x": 1}
    assert d["dialed_address"] == "10.0.0.1"
    assert d["mode"] == "vpn"
