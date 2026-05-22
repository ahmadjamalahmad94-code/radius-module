"""Tests for K1.3 — VPN reachability probe.

We don't run real `wg` / `ping` here — the probes themselves are
intentionally defensive and degrade to `None` when the binaries
are missing. These tests pin that behaviour AND verify the
status-bucket logic against synthetic inputs.
"""
from __future__ import annotations

from unittest.mock import patch

from app.radius.services import vpn_probe
from app.radius.services.vpn_probe import (
    FRESH_HANDSHAKE_MAX,
    SLOW_HANDSHAKE_MAX,
    VpnStatus,
    clear_caches,
    is_peer_alive,
    latest_handshake_age,
    status_for,
)


def setup_function(_):
    clear_caches()


def test_is_peer_alive_returns_none_without_ping_binary():
    """When `ping` isn't on PATH the probe must return None,
    NOT raise. Operator UI shows the unknown chip."""
    with patch("shutil.which", return_value=None):
        assert is_peer_alive("10.10.0.5") is None


def test_is_peer_alive_empty_input():
    assert is_peer_alive("") is None


def test_latest_handshake_age_returns_none_without_wg_binary():
    with patch("shutil.which", return_value=None):
        assert latest_handshake_age("any-pub-key") is None


def test_latest_handshake_age_parses_wg_dump():
    """Synthetic `wg show wg0 dump` output. The Unix epoch in the
    `latest-handshake` column is what we expect to read."""
    fake_dump = (
        "iface_priv\tiface_pub\t51820\toff\n"
        "abc123\t(none)\t1.2.3.4:51820\t10.0.0.5/32\t1700000000\t100\t200\t25\n"
        "def456\t(none)\t1.2.3.5:51820\t10.0.0.6/32\t1700000050\t200\t400\t25\n"
    )

    class _Result:
        returncode = 0
        stdout = fake_dump

    with patch("shutil.which", return_value="/usr/bin/wg"), \
         patch("subprocess.run", return_value=_Result()), \
         patch("app.radius.services.vpn_probe._now",
               return_value=1700000100):
        age_a = latest_handshake_age("abc123")
        age_b = latest_handshake_age("def456")
        missing = latest_handshake_age("ghi789")
    assert age_a == 100
    assert age_b == 50
    assert missing is None


def test_status_bucket_fresh():
    with patch.object(vpn_probe, "latest_handshake_age",
                       return_value=30):
        s = status_for(public_key="abc")
    assert s.bucket == "fresh"
    assert s.handshake_age_sec == 30
    assert s.is_fresh is True


def test_status_bucket_slow():
    with patch.object(vpn_probe, "latest_handshake_age",
                       return_value=FRESH_HANDSHAKE_MAX + 60):
        s = status_for(public_key="abc")
    assert s.bucket == "slow"


def test_status_bucket_stale():
    with patch.object(vpn_probe, "latest_handshake_age",
                       return_value=SLOW_HANDSHAKE_MAX + 60):
        s = status_for(public_key="abc")
    assert s.bucket == "stale"
    assert s.is_stale is True


def test_status_falls_back_to_ping_when_no_handshake():
    """Peer just connected — handshake age unknown — but ping
    works → 'slow' bucket with the explanatory note."""
    with patch.object(vpn_probe, "latest_handshake_age",
                       return_value=None), \
         patch.object(vpn_probe, "is_peer_alive", return_value=True):
        s = status_for(public_key="abc", peer_ip="10.10.0.5")
    assert s.bucket == "slow"
    assert s.peer_alive is True
    assert "ping" in s.note


def test_status_stale_when_both_signals_dark():
    with patch.object(vpn_probe, "latest_handshake_age",
                       return_value=None), \
         patch.object(vpn_probe, "is_peer_alive", return_value=False):
        s = status_for(public_key="abc", peer_ip="10.10.0.5")
    assert s.bucket == "stale"


def test_status_unknown_when_no_tooling():
    with patch.object(vpn_probe, "latest_handshake_age",
                       return_value=None), \
         patch.object(vpn_probe, "is_peer_alive", return_value=None):
        s = status_for(public_key="abc", peer_ip="10.10.0.5")
    assert s.bucket == "unknown"
    assert s.handshake_age_sec is None
    assert s.peer_alive is None


def test_vpn_status_is_immutable_dataclass():
    s = VpnStatus("fresh", 10, True, "")
    # Frozen — attribute assignment must fail at runtime.
    try:
        s.bucket = "stale"  # type: ignore[misc]
    except (AttributeError, TypeError):
        return
    raise AssertionError("VpnStatus should be frozen / immutable")
