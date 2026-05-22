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


# ─── K4: interfaces + network fetcher tests ──────────────────────


def test_interface_list_calls_interface_print(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {"name": "ether1", "rx-byte": "1000", "tx-byte": "500", "running": "true"},
        {"name": "wg0", "rx-byte": "200", "tx-byte": "100", "running": "true"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.interface_list(fake_nas_direct)

    assert res.ok is True
    assert len(res.data) == 2
    assert res.data[0]["name"] == "ether1"
    mock_client.print_.assert_called_once_with("/interface/print")


def test_interface_traffic_runs_monitor_traffic_once(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [
        {"reply": "!re", "attrs": {
            "name": "ether1",
            "rx-bits-per-second": "1234567",
            "tx-bits-per-second": "987654",
        }},
        {"reply": "!done", "attrs": {}},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.interface_traffic(fake_nas_direct, "ether1")

    assert res.ok is True
    assert res.data == [{
        "name": "ether1",
        "rx-bits-per-second": "1234567",
        "tx-bits-per-second": "987654",
    }]
    # Make sure the right RouterOS command + attrs were sent.
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/interface/monitor-traffic"
    assert kwargs["attrs"]["interface"] == "ether1"
    assert "once" in kwargs["attrs"]


def test_interface_traffic_empty_name_short_circuits(fake_nas_direct):
    """Don't dial the router when the caller forgot the interface
    name — the route handler still gets a clean envelope."""
    res = mac.interface_traffic(fake_nas_direct, "")
    assert res.ok is False
    assert "غير محدد" in res.error


def test_interface_traffic_cache_is_per_interface(fake_nas_direct):
    """Two different interfaces must not share a cache slot."""
    mock_client = MagicMock()
    mock_client.run.return_value = [
        {"reply": "!re", "attrs": {"rx-bits-per-second": "1"}},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.interface_traffic(fake_nas_direct, "ether1")
        mac.interface_traffic(fake_nas_direct, "ether2")
        # Re-hitting the first one must come from cache.
        again = mac.interface_traffic(fake_nas_direct, "ether1")

    assert mock_client.run.call_count == 2
    assert again.cached is True


def test_ip_addresses_calls_ip_address_print(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {"address": "10.10.0.5/24", "interface": "wg0"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.ip_addresses(fake_nas_direct)

    assert res.ok is True
    mock_client.print_.assert_called_once_with("/ip/address/print")
    assert res.data[0]["interface"] == "wg0"


def test_ip_routes_calls_ip_route_print(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {"dst-address": "0.0.0.0/0", "gateway": "10.10.0.1", "active": "true"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.ip_routes(fake_nas_direct)

    assert res.ok is True
    mock_client.print_.assert_called_once_with("/ip/route/print")
    assert res.data[0]["gateway"] == "10.10.0.1"


# ─── K4.2: SSE generator tests ───────────────────────────────────


def test_stream_interface_samples_yields_max_samples(fake_nas_direct):
    """Generator emits exactly `max_samples` snapshots, sleeping
    between them — and the injected `_sleep` is invoked one less
    time than samples (no trailing sleep on the last yield)."""
    mock_client = MagicMock()
    mock_client.run.return_value = [
        {"reply": "!re", "attrs": {"rx-bits-per-second": "100"}},
    ]
    fake = _patched_pool(mock_client)
    sleeps: list[float] = []

    with patch.object(mac, "_pool_acquire", fake):
        samples = list(mac.stream_interface_samples(
            fake_nas_direct, "ether1",
            period_sec=0.0, max_samples=3,
            _sleep=sleeps.append,
        ))

    assert len(samples) == 3
    assert all(s.ok for s in samples)
    # 3 samples → 2 inter-sample sleeps
    assert sleeps == [0.0, 0.0]
    assert mock_client.run.call_count == 3


def test_stream_interface_samples_bypasses_cache(fake_nas_direct):
    """The SSE loop must NOT serve from cache — each tick is a
    fresh dial. Pre-warm the regular cached fetcher first, then
    confirm streaming still hits the wire twice."""
    mock_client = MagicMock()
    mock_client.run.return_value = [
        {"reply": "!re", "attrs": {"rx-bits-per-second": "1"}},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        # Warm the cached endpoint to prove the SSE loop ignores it.
        mac.interface_traffic(fake_nas_direct, "ether1")
        list(mac.stream_interface_samples(
            fake_nas_direct, "ether1",
            period_sec=0.0, max_samples=2,
            _sleep=lambda _s: None,
        ))

    # 1 cached + 2 streamed = 3 wire calls total.
    assert mock_client.run.call_count == 3


def test_stream_interface_samples_stops_on_error(fake_nas_direct):
    """If the router goes away mid-stream, yield ONE error envelope
    and then stop — don't loop forever hammering a dead router."""
    from contextlib import contextmanager
    from app.radius.integration.mikrotik.errors import ConnectError

    calls = []

    @contextmanager
    def fake_acquire(cfg):
        calls.append(1)
        raise ConnectError("router gone")
        yield  # pragma: no cover

    with patch.object(mac, "_pool_acquire", fake_acquire):
        samples = list(mac.stream_interface_samples(
            fake_nas_direct, "ether1",
            period_sec=0.0, max_samples=10,
            _sleep=lambda _s: None,
        ))

    assert len(samples) == 1
    assert samples[0].ok is False
    assert "تعذر الاتصال" in samples[0].error
    assert len(calls) == 1  # didn't retry


def test_stream_interface_samples_rejects_empty_name(fake_nas_direct):
    samples = list(mac.stream_interface_samples(
        fake_nas_direct, "",
        period_sec=0.0, max_samples=5,
        _sleep=lambda _s: None,
    ))
    assert len(samples) == 1
    assert samples[0].ok is False
    assert "غير محدد" in samples[0].error
