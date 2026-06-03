"""QA: bidirectional session sync helpers (mt_reconciler).

Piece 1: RouterOS uptime parsing + active-row mapping (pure functions).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.workers.mt_reconciler import (  # noqa: E402
    _keys_from_rows,
    _map_active_rows,
    _parse_ros_uptime,
)


def test_parse_uptime_unit_form():
    assert _parse_ros_uptime("22m54s") == 22 * 60 + 54
    assert _parse_ros_uptime("1h2m3s") == 3723
    assert _parse_ros_uptime("3h") == 10800
    assert _parse_ros_uptime("1w2d3h4m5s") == 604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert _parse_ros_uptime("") == 0
    assert _parse_ros_uptime("junk") == 0


def test_parse_uptime_colon_form():
    assert _parse_ros_uptime("00:22:54") == 22 * 60 + 54
    assert _parse_ros_uptime("1:02:03") == 3723


def test_map_hotspot_and_ppp_rows():
    hot = [{"user": "ahmad", "mac-address": "9e:49:36:50:27:a4",
            "address": "10.19.6.254", "uptime": "22m54s",
            "bytes-in": "1000", "bytes-out": "2000"}]
    ppp = [{"name": "pppuser", "caller-id": "AA:BB:CC:DD:EE:FF",
            "address": "10.20.0.5", "uptime": "1h"}]
    rows = _map_active_rows(hot, ppp)
    assert len(rows) == 2
    h = rows[0]
    assert h["username"] == "ahmad"
    assert h["mac"] == "9E:49:36:50:27:A4"        # normalized upper/colon
    assert h["framed_ip"] == "10.19.6.254"
    assert h["uptime_sec"] == 22 * 60 + 54
    assert h["bytes_in"] == 1000 and h["bytes_out"] == 2000
    assert h["source"] == "hotspot"
    p = rows[1]
    assert p["username"] == "pppuser" and p["mac"] == "AA:BB:CC:DD:EE:FF"
    assert p["source"] == "ppp" and p["framed_ip"] == "10.20.0.5"


def test_map_skips_rows_without_username():
    rows = _map_active_rows([{"mac-address": "aa:bb:cc:dd:ee:ff"}], [{"caller-id": "x"}])
    assert rows == []


def test_keys_from_rows_lowercases_user_uppercases_mac():
    rows = _map_active_rows(
        [{"user": "Ahmad", "mac-address": "9e:49:36:50:27:a4", "uptime": "1m"}], [])
    assert _keys_from_rows(rows) == {("ahmad", "9E:49:36:50:27:A4")}
