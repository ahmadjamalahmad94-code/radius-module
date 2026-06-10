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


# ─── K5: hotspot + PPP active reads ─────────────────────────────


def test_hotspot_active_calls_right_path(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {".id": "*1", "user": "u-1", "address": "10.5.0.10", "bytes-in": "100"},
        {".id": "*2", "user": "u-2", "address": "10.5.0.11", "bytes-in": "200"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.hotspot_active(fake_nas_direct)

    assert res.ok is True
    assert len(res.data) == 2
    mock_client.print_.assert_called_once_with("/ip/hotspot/active/print")


def test_disconnect_hotspot_invokes_remove(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.disconnect_hotspot_session(fake_nas_direct, "*7")

    assert res.ok is True
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/ip/hotspot/active/remove"
    assert kwargs["attrs"][".id"] == "*7"


def test_disconnect_hotspot_empty_id_short_circuits(fake_nas_direct):
    res = mac.disconnect_hotspot_session(fake_nas_direct, "")
    assert res.ok is False
    assert "غير محدد" in res.error


def test_disconnect_hotspot_invalidates_active_cache(fake_nas_direct):
    """Successful disconnect must drop the cached active list so the
    next /hotspot/active read reflects the kick immediately."""
    mock_client = MagicMock()
    mock_client.print_.return_value = [{".id": "*1", "user": "a"}]
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        # Warm the cache.
        mac.hotspot_active(fake_nas_direct)
        # Kick someone.
        mac.disconnect_hotspot_session(fake_nas_direct, "*1")
        # Next read must re-hit the wire (cache was dropped).
        mac.hotspot_active(fake_nas_direct)

    # Two print_ calls = re-fetch happened.
    assert mock_client.print_.call_count == 2


def test_disconnect_hotspot_keeps_cache_when_router_rejects(fake_nas_direct):
    """Trap means 'router rejected the remove'. Don't drop the cache
    — last-known-good list is more useful than re-fetching to get
    the same row back."""
    from contextlib import contextmanager
    from app.radius.integration.mikrotik.errors import MikrotikTrap

    # Warm the cache via a working mock first.
    list_client = MagicMock()
    list_client.print_.return_value = [{".id": "*1"}]
    fake_list = _patched_pool(list_client)
    with patch.object(mac, "_pool_acquire", fake_list):
        mac.hotspot_active(fake_nas_direct)

    @contextmanager
    def trap_acquire(cfg):
        client = MagicMock()
        client.run.side_effect = MikrotikTrap("no such item")
        yield client

    with patch.object(mac, "_pool_acquire", trap_acquire):
        res = mac.disconnect_hotspot_session(fake_nas_direct, "*999")

    assert res.ok is False

    # Cache survived — next list comes from cache (no wire call).
    with patch.object(mac, "_pool_acquire", fake_list):
        again = mac.hotspot_active(fake_nas_direct)
    assert again.cached is True
    # Only the original warming call hit the wire.
    assert list_client.print_.call_count == 1


def test_disconnect_ppp_invokes_remove(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.disconnect_ppp_session(fake_nas_direct, "*42")

    assert res.ok is True
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/ppp/active/remove"
    assert kwargs["attrs"][".id"] == "*42"


def test_ppp_active_calls_right_path(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {".id": "*1", "name": "home-001", "address": "10.6.0.10"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.ppp_active(fake_nas_direct)

    assert res.ok is True
    assert res.data[0]["name"] == "home-001"
    mock_client.print_.assert_called_once_with("/ppp/active/print")


# ─── K6.1: simple queues ─────────────────────────────────────────


def test_queue_simple_list_calls_right_path(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {".id": "*1", "name": "q1", "target": "10.5.0.0/24",
         "max-limit": "20M/20M", "disabled": "false"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.queue_simple_list(fake_nas_direct)

    assert res.ok is True
    mock_client.print_.assert_called_once_with("/queue/simple/print")
    assert res.data[0]["max-limit"] == "20M/20M"


def test_queue_simple_set_sends_id_and_allowed_attrs(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.queue_simple_set(
            fake_nas_direct, "*3",
            {"max-limit": "30M/30M", "disabled": True},
        )

    assert res.ok is True
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/queue/simple/set"
    assert kwargs["attrs"][".id"] == "*3"
    assert kwargs["attrs"]["max-limit"] == "30M/30M"
    # disabled coerced bool→'yes'.
    assert kwargs["attrs"]["disabled"] == "yes"


def test_queue_simple_set_rejects_forbidden_fields(fake_nas_direct):
    """Editing parent/target/type is dangerous — refuse early."""
    res = mac.queue_simple_set(
        fake_nas_direct, "*3", {"target": "0.0.0.0/0"},
    )
    assert res.ok is False
    assert "غير مسموح" in res.error


def test_queue_simple_set_rejects_empty_id(fake_nas_direct):
    res = mac.queue_simple_set(fake_nas_direct, "", {"disabled": False})
    assert res.ok is False
    assert "غير محدد" in res.error


def test_queue_simple_set_rejects_empty_attrs(fake_nas_direct):
    res = mac.queue_simple_set(fake_nas_direct, "*3", {})
    assert res.ok is False
    assert "لا توجد حقول" in res.error


def test_queue_simple_set_rejects_bad_disabled(fake_nas_direct):
    res = mac.queue_simple_set(
        fake_nas_direct, "*3", {"disabled": "maybe"},
    )
    assert res.ok is False
    assert "true/false" in res.error


def test_queue_simple_set_invalidates_list_cache(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{".id": "*1", "max-limit": "1M/1M"}]
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.queue_simple_list(fake_nas_direct)
        mac.queue_simple_set(
            fake_nas_direct, "*1", {"max-limit": "5M/5M"},
        )
        mac.queue_simple_list(fake_nas_direct)

    assert mock_client.print_.call_count == 2


# ─── K6.2: firewall + address-list ───────────────────────────────


def test_firewall_filter_calls_right_path(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {".id": "*1", "chain": "input", "action": "accept"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.firewall_filter(fake_nas_direct)

    assert res.ok is True
    mock_client.print_.assert_called_once_with("/ip/firewall/filter/print")


def test_firewall_nat_calls_right_path(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {".id": "*1", "chain": "srcnat", "action": "masquerade"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.firewall_nat(fake_nas_direct)

    assert res.ok is True
    mock_client.print_.assert_called_once_with("/ip/firewall/nat/print")


def test_address_list_list_calls_right_path(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {".id": "*1", "list": "blocked", "address": "1.1.1.1"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.address_list_list(fake_nas_direct)

    assert res.ok is True
    mock_client.print_.assert_called_once_with(
        "/ip/firewall/address-list/print",
    )


def test_address_list_add_sends_attrs(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.address_list_add(
            fake_nas_direct,
            list_name="blocked", address="2.2.2.2",
            comment="bad bot", timeout="1h",
        )

    assert res.ok is True
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/ip/firewall/address-list/add"
    assert kwargs["attrs"]["list"] == "blocked"
    assert kwargs["attrs"]["address"] == "2.2.2.2"
    assert kwargs["attrs"]["comment"] == "bad bot"
    assert kwargs["attrs"]["timeout"] == "1h"


def test_address_list_add_rejects_empty(fake_nas_direct):
    res = mac.address_list_add(
        fake_nas_direct, list_name="", address="1.2.3.4",
    )
    assert res.ok is False
    assert "اسم القائمة" in res.error

    res2 = mac.address_list_add(
        fake_nas_direct, list_name="blocked", address="",
    )
    assert res2.ok is False
    assert "العنوان" in res2.error


def test_address_list_add_invalidates_cache(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{".id": "*1"}]
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.address_list_list(fake_nas_direct)
        mac.address_list_add(
            fake_nas_direct, list_name="x", address="3.3.3.3",
        )
        mac.address_list_list(fake_nas_direct)

    assert mock_client.print_.call_count == 2


def test_address_list_remove_sends_id(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.address_list_remove(fake_nas_direct, "*9")

    assert res.ok is True
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/ip/firewall/address-list/remove"
    assert kwargs["attrs"][".id"] == "*9"


def test_address_list_remove_rejects_empty_id(fake_nas_direct):
    res = mac.address_list_remove(fake_nas_direct, "")
    assert res.ok is False
    assert "غير محدد" in res.error


# ─── K7.1: log tail ──────────────────────────────────────────────


def test_log_tail_returns_all_when_no_topics(fake_nas_direct):
    rows = [{"topics": "system,info", "message": str(i)} for i in range(5)]
    mock_client = MagicMock()
    mock_client.print_.return_value = rows
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.log_tail(fake_nas_direct, limit=10)

    assert res.ok is True
    assert len(res.data) == 5
    mock_client.print_.assert_called_once_with("/log/print")


def test_log_tail_filters_by_topic(fake_nas_direct):
    rows = [
        {"topics": "system,info", "message": "a"},
        {"topics": "firewall,debug", "message": "b"},
        {"topics": "system,error", "message": "c"},
    ]
    mock_client = MagicMock()
    mock_client.print_.return_value = rows
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.log_tail(fake_nas_direct, topics=["firewall"], limit=100)

    assert res.ok is True
    assert len(res.data) == 1
    assert res.data[0]["message"] == "b"


def test_log_tail_respects_limit(fake_nas_direct):
    rows = [{"topics": "system", "message": str(i)} for i in range(50)]
    mock_client = MagicMock()
    mock_client.print_.return_value = rows
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.log_tail(fake_nas_direct, limit=10)

    # Tail (last N), not head.
    assert len(res.data) == 10
    assert res.data[0]["message"] == "40"
    assert res.data[-1]["message"] == "49"


def test_log_tail_cache_key_per_filter(fake_nas_direct):
    """Different (topics, limit) combos must NOT share a cache slot."""
    rows = [{"topics": "system", "message": "x"}]
    mock_client = MagicMock()
    mock_client.print_.return_value = rows
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.log_tail(fake_nas_direct, topics=["system"], limit=10)
        mac.log_tail(fake_nas_direct, topics=["firewall"], limit=10)
        mac.log_tail(fake_nas_direct, topics=["system"], limit=20)

    assert mock_client.print_.call_count == 3


# ─── K8.1: file list + backup save ───────────────────────────────


def test_file_list_calls_file_print(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [
        {".id": "*1", "name": "backup-20260101.backup",
         "type": "backup", "size": "12345"},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.file_list(fake_nas_direct)

    assert res.ok is True
    mock_client.print_.assert_called_once_with("/file/print")
    assert res.data[0]["type"] == "backup"


def test_backup_save_sends_name(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.backup_save(fake_nas_direct, name="backup-x1")

    assert res.ok is True
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/system/backup/save"
    assert kwargs["attrs"]["name"] == "backup-x1"


def test_backup_save_invalidates_file_list_cache(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{".id": "*1"}]
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.file_list(fake_nas_direct)
        mac.backup_save(fake_nas_direct, name="b1")
        mac.file_list(fake_nas_direct)

    assert mock_client.print_.call_count == 2


@pytest.mark.parametrize("bad", [
    "", "   ",                  # empty
    "../etc/passwd",            # traversal
    "back/sub",                 # slash
    "back\\sub",                # backslash
    "..hidden",                 # leading dots
    ".hidden",                  # leading dot
    "name\x00null",             # control char
    "x" * 200,                  # too long
    "@badname",                 # punctuation outside allowlist
])
def test_backup_save_rejects_unsafe_names(fake_nas_direct, bad):
    res = mac.backup_save(fake_nas_direct, name=bad)
    assert res.ok is False
    assert res.error  # message present


def test_backup_save_accepts_normal_names(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        for good in ("backup1", "B-2026", "weekly_07",
                     "snap.20260101", "abc-DEF_g.h"):
            res = mac.backup_save(fake_nas_direct, name=good)
            assert res.ok is True, f"{good!r} should be allowed"


# ─── K8.1b: file download (honest unsupported) ───────────────────


class _FakeFTP:
    """Minimal stand-in for ftplib.FTP that serves a fixed payload."""

    def __init__(self, payload=b"", *, perm_error=False):
        self._payload = payload
        self._perm_error = perm_error
        self.quit_called = False
        self.retr_arg = None

    def retrbinary(self, cmd, callback, blocksize=8192):
        import ftplib
        self.retr_arg = cmd
        if self._perm_error:
            raise ftplib.error_perm("550 No such file")
        for i in range(0, len(self._payload), blocksize):
            callback(self._payload[i:i + blocksize])

    def quit(self):
        self.quit_called = True


def test_file_download_stream_returns_bytes_over_ftp(fake_nas_direct, monkeypatch):
    """Real FTP path: the helper streams the router file's bytes and
    reports the size — never fabricated/empty bytes."""
    payload = b"BACKUP-BINARY-CONTENT" * 5000  # ~100KB, exercises chunking
    fake = _FakeFTP(payload)
    monkeypatch.setattr(mac, "_ftp_connect",
                        lambda *a, **k: fake)

    size, stream = mac.file_download_stream(fake_nas_direct, "weekly.backup")
    collected = b"".join(stream)

    assert size == len(payload)
    assert collected == payload
    assert fake.retr_arg == "RETR weekly.backup"
    assert fake.quit_called is True


def test_file_download_stream_missing_file_raises_download_error(fake_nas_direct, monkeypatch):
    monkeypatch.setattr(mac, "_ftp_connect",
                        lambda *a, **k: _FakeFTP(perm_error=True))
    with pytest.raises(mac.FileDownloadError):
        size, stream = mac.file_download_stream(fake_nas_direct, "missing.backup")
        list(stream)


def test_file_download_stream_no_address_raises_not_supported(fake_nas_direct, monkeypatch):
    """A router with no resolvable address is the only path that still
    raises FileDownloadNotSupported (cannot even be dialed)."""
    monkeypatch.setattr(mac, "resolve_connection_address", lambda nas: "")
    with pytest.raises(mac.FileDownloadNotSupported):
        mac.file_download_stream(fake_nas_direct, "any.backup")


# ─── K8.2: reboot + identity ─────────────────────────────────────


def test_system_reboot_sends_command_and_drops_system_caches(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{"cpu-load": "5"}]
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        # Warm the system/resource cache.
        mac.system_resource(fake_nas_direct)
        res = mac.system_reboot(fake_nas_direct)
        # Re-read; cache must have been invalidated.
        mac.system_resource(fake_nas_direct)

    assert res.ok is True
    args, _ = mock_client.run.call_args
    assert args[0] == "/system/reboot"
    assert mock_client.print_.call_count == 2


def test_system_identity_set_sends_name(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.system_identity_set(fake_nas_direct, name="main-gw")

    assert res.ok is True
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/system/identity/set"
    assert kwargs["attrs"]["name"] == "main-gw"


@pytest.mark.parametrize("bad", [
    "", "   ",
    "name with spaces",
    "bad/slash",
    "ctl\x01char",
    "x" * 64,
])
def test_system_identity_set_rejects_bad_names(fake_nas_direct, bad):
    res = mac.system_identity_set(fake_nas_direct, name=bad)
    assert res.ok is False
    assert res.error


def test_system_identity_set_invalidates_identity_cache(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.print_.return_value = [{"name": "old"}]
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.system_identity(fake_nas_direct)
        mac.system_identity_set(fake_nas_direct, name="new")
        mac.system_identity(fake_nas_direct)

    assert mock_client.print_.call_count == 2


# ─── K7.2: diagnostic tools ──────────────────────────────────────


def test_tool_ping_sends_address_and_count(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [
        {"reply": "!re", "attrs": {"seq": "0", "time": "12ms"}},
        {"reply": "!re", "attrs": {"seq": "1", "time": "11ms"}},
        {"reply": "!done", "attrs": {}},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.tool_ping(fake_nas_direct, target="8.8.8.8", count=2)

    assert res.ok is True
    assert len(res.data) == 2
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/ping"
    assert kwargs["attrs"]["address"] == "8.8.8.8"
    assert kwargs["attrs"]["count"] == "2"


def test_tool_ping_caps_count(fake_nas_direct):
    """Operator cannot ask for 1 000 packets - cap at PING_MAX_COUNT."""
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.tool_ping(fake_nas_direct, target="1.1.1.1", count=10_000)

    _, kwargs = mock_client.run.call_args
    assert kwargs["attrs"]["count"] == str(mac.PING_MAX_COUNT)


def test_tool_ping_rejects_empty_target(fake_nas_direct):
    res = mac.tool_ping(fake_nas_direct, target="")
    assert res.ok is False
    assert "غير محدد" in res.error


def test_tool_traceroute_sends_address(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [
        {"reply": "!re", "attrs": {"address": "10.0.0.1"}},
        {"reply": "!re", "attrs": {"address": "1.1.1.1"}},
        {"reply": "!done", "attrs": {}},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.tool_traceroute(fake_nas_direct, target="1.1.1.1")

    assert res.ok is True
    assert len(res.data) == 2
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/tool/traceroute"
    assert kwargs["attrs"]["address"] == "1.1.1.1"


def test_tool_traceroute_caps_count(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.tool_traceroute(fake_nas_direct, target="1.1.1.1", count=99)

    _, kwargs = mock_client.run.call_args
    assert kwargs["attrs"]["count"] == str(mac.TRACEROUTE_MAX_COUNT)


def test_tool_dns_resolve_sends_name(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [
        {"reply": "!re", "attrs": {"name": "example.com", "address": "1.2.3.4"}},
        {"reply": "!done", "attrs": {}},
    ]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        res = mac.tool_dns_resolve(fake_nas_direct, name="example.com")

    assert res.ok is True
    args, kwargs = mock_client.run.call_args
    assert args[0] == "/resolve"
    assert kwargs["attrs"]["name"] == "example.com"
    assert "server" not in kwargs["attrs"]


def test_tool_dns_resolve_with_custom_server(fake_nas_direct):
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.tool_dns_resolve(
            fake_nas_direct, name="example.com", server="8.8.8.8",
        )

    _, kwargs = mock_client.run.call_args
    assert kwargs["attrs"]["server"] == "8.8.8.8"


def test_tool_dns_resolve_rejects_empty_name(fake_nas_direct):
    res = mac.tool_dns_resolve(fake_nas_direct, name="  ")
    assert res.ok is False
    assert "اسم النطاق" in res.error


def test_diagnostics_are_never_cached(fake_nas_direct):
    """Each ping/traceroute/resolve call must hit the wire fresh
    even with identical args - cached diagnostics defeat the point."""
    mock_client = MagicMock()
    mock_client.run.return_value = [{"reply": "!done", "attrs": {}}]
    fake = _patched_pool(mock_client)

    with patch.object(mac, "_pool_acquire", fake):
        mac.tool_ping(fake_nas_direct, target="1.1.1.1", count=1)
        mac.tool_ping(fake_nas_direct, target="1.1.1.1", count=1)
        mac.tool_traceroute(fake_nas_direct, target="1.1.1.1")
        mac.tool_traceroute(fake_nas_direct, target="1.1.1.1")
        mac.tool_dns_resolve(fake_nas_direct, name="a.com")
        mac.tool_dns_resolve(fake_nas_direct, name="a.com")

    assert mock_client.run.call_count == 6


def test_stream_interface_samples_rejects_empty_name(fake_nas_direct):
    samples = list(mac.stream_interface_samples(
        fake_nas_direct, "",
        period_sec=0.0, max_samples=5,
        _sleep=lambda _s: None,
    ))
    assert len(samples) == 1
    assert samples[0].ok is False
    assert "غير محدد" in samples[0].error
