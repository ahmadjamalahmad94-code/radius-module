"""O1 — counters service + endpoint."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.radius.services import mikrotik_admin_client as mac
from app.radius.services import mt_counters


@pytest.fixture(autouse=True)
def _reset_cache():
    mac._cache.clear()
    yield
    mac._cache.clear()


@pytest.fixture
def fake_nas():
    return {
        "id": 1,
        "address": "10.10.0.99",
        "connection_mode": "direct",
        "api_port": 8728,
        "api_user": "admin",
        "api_password": "x",
        "api_use_tls": 0,
    }


def _pool(mock_client):
    """Patch _pool_acquire to yield the given mock client."""
    @contextmanager
    def fake(_cfg):
        yield mock_client
    return fake


def _wire_three_endpoints(mock_client, *,
                          hotspot_rows=(), ppp_rows=(), iface_rows=()):
    """Route the three print_ paths to their respective fixtures.

    `print_` is called with a single positional path string; we
    side-effect on it so the same mock serves all three endpoints
    of counters_for_nas in one go.
    """
    def by_path(path, *a, **kw):
        if path == "/ip/hotspot/active/print":
            return iter(list(hotspot_rows))
        if path == "/ppp/active/print":
            return iter(list(ppp_rows))
        if path == "/interface/print":
            return iter(list(iface_rows))
        return iter([])
    mock_client.print_.side_effect = by_path


# ─── Aggregation maths ───────────────────────────────────────────


def test_counters_happy_path(fake_nas):
    mc = MagicMock()
    _wire_three_endpoints(
        mc,
        hotspot_rows=[{".id": "*1"}, {".id": "*2"}, {".id": "*3"}],
        ppp_rows=[{".id": "*9"}],
        iface_rows=[
            {"name": "ether1", "rx-byte": "1500", "tx-byte": "300"},
            {"name": "ether2", "rx-byte": "200",  "tx-byte": "100"},
        ],
    )

    with patch.object(mac, "_pool_acquire", _pool(mc)):
        res = mt_counters.counters_for_nas(fake_nas)

    assert res.ok is True
    d = res.data
    assert d["hotspot_active"] == 3
    assert d["ppp_active"] == 1
    assert d["rx_bytes_total"] == 1700   # 1500 + 200
    assert d["tx_bytes_total"] == 400    # 300 + 100
    assert isinstance(d["fetched_at"], float)
    assert res.dialed_address == "10.10.0.99"


def test_counters_handle_unparseable_byte_strings(fake_nas):
    """RouterOS sometimes returns 'unknown' or empty in byte
    fields — those count as 0 instead of crashing the call."""
    mc = MagicMock()
    _wire_three_endpoints(
        mc,
        iface_rows=[
            {"name": "ether1", "rx-byte": "500",     "tx-byte": "200"},
            {"name": "ether2", "rx-byte": "garbage", "tx-byte": ""},
            {"name": "ether3", "rx-byte": None,      "tx-byte": "  "},
        ],
    )
    with patch.object(mac, "_pool_acquire", _pool(mc)):
        res = mt_counters.counters_for_nas(fake_nas)
    assert res.data["rx_bytes_total"] == 500
    assert res.data["tx_bytes_total"] == 200


def test_counters_partial_failure_marks_envelope_not_ok(fake_nas):
    """If interface_list fails but hotspot+ppp succeed, counters
    return what they could collect with ok=False + an Arabic
    error string the UI can show."""
    from contextlib import contextmanager
    from app.radius.integration.mikrotik.errors import ConnectError

    @contextmanager
    def fake_acquire(_cfg):
        # First two calls succeed via a happy mock; third raises.
        # Simplest setup: route by path.
        client = MagicMock()
        def by_path(path, *a, **kw):
            if path == "/interface/print":
                raise ConnectError("link down")
            if path == "/ip/hotspot/active/print":
                return iter([{".id": "*1"}])
            if path == "/ppp/active/print":
                return iter([])
            return iter([])
        client.print_.side_effect = by_path
        yield client

    with patch.object(mac, "_pool_acquire", fake_acquire):
        res = mt_counters.counters_for_nas(fake_nas)

    assert res.ok is False
    assert "interfaces" in res.error
    # Partial data still surfaces:
    assert res.data["hotspot_active"] == 1
    assert res.data["ppp_active"] == 0
    assert res.data["rx_bytes_total"] == 0   # interface fetch failed
    assert res.data["tx_bytes_total"] == 0


def test_counters_reuse_cached_values(fake_nas):
    """Two back-to-back counter calls within the TTL window must
    hit the wire only once per sub-endpoint — proves the K3+K4
    cache wrapper is being used."""
    mc = MagicMock()
    _wire_three_endpoints(mc,
                           hotspot_rows=[{".id": "*1"}],
                           ppp_rows=[],
                           iface_rows=[{"rx-byte": "10", "tx-byte": "5"}])
    with patch.object(mac, "_pool_acquire", _pool(mc)):
        first  = mt_counters.counters_for_nas(fake_nas)
        second = mt_counters.counters_for_nas(fake_nas)

    assert first.ok and second.ok
    assert second.cached is True
    # 3 sub-fetches × 1 wire call each (first time only).
    assert mc.print_.call_count == 3
