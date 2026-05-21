"""Tests for app.radius.core.units — see SERVICES_COOKBOOK §18."""
from __future__ import annotations

import pytest

from app.radius.core.units import (
    UnitError, best_unit, format_human, format_pair, from_base, to_base,
)


# ─────────────────────────── to_base / from_base ───────────────
def test_speed_roundtrip():
    assert to_base(5, "Mbps", "speed") == 5120
    assert from_base(5120, "Mbps", "speed") == 5.0
    assert from_base(5120, "kbps", "speed") == 5120.0


def test_quota_roundtrip():
    assert to_base(1, "GB", "quota") == 1024
    assert to_base(2, "TB", "quota") == 2 * 1024 * 1024
    assert from_base(1024, "GB", "quota") == 1.0


def test_time_roundtrip():
    assert to_base(2, "hr",    "time") == 120
    assert to_base(1, "day",   "time") == 60 * 24
    assert to_base(1, "month", "time") == 60 * 24 * 30
    assert from_base(120, "hr", "time") == 2.0


def test_size_roundtrip():
    assert to_base(1, "MB", "size") == 1024
    assert to_base(1, "GB", "size") == 1024 * 1024


def test_to_base_accepts_floats():
    assert to_base(0.5, "Mbps", "speed") == 512


def test_to_base_rejects_unknown_kind_or_unit():
    with pytest.raises(UnitError):
        to_base(1, "Mbps", "fake_kind")
    with pytest.raises(UnitError):
        to_base(1, "fakeunit", "speed")


# ─────────────────────────── best_unit ─────────────────────────
def test_best_unit_picks_largest_clean_unit():
    assert best_unit(5120,        "speed") == "Mbps"      # 5 Mbps
    assert best_unit(1024 * 1024, "speed") == "Gbps"      # 1 Gbps
    assert best_unit(1024,        "quota") == "GB"        # 1 GB
    assert best_unit(60 * 24,     "time")  == "day"       # 1 day


def test_best_unit_falls_back_to_base_when_not_clean():
    # 5121 kbps isn't a clean number of Mbps → stay in kbps
    assert best_unit(5121, "speed") == "kbps"
    # 1025 MB → stay in MB
    assert best_unit(1025, "quota") == "MB"


def test_best_unit_zero_returns_smallest_unit():
    assert best_unit(0, "speed") == "kbps"
    assert best_unit(0, "quota") == "MB"
    assert best_unit(0, "time")  == "min"


def test_best_unit_rejects_unknown_kind():
    with pytest.raises(UnitError):
        best_unit(100, "fake")


# ─────────────────────────── format helpers ────────────────────
def test_format_pair_returns_tuple():
    val, unit = format_pair(5120, "speed")
    assert (val, unit) == (5.0, "Mbps")


def test_format_human_drops_trailing_zero_and_uses_arabic_for_time():
    assert format_human(5120, "speed") == "5 Mbps"
    assert format_human(60,   "time")  == "1 ساعات"
    assert format_human(1024, "quota") == "1 GB"
    assert format_human(0,    "speed") == "0 Kbps"


def test_format_human_keeps_decimals_when_needed():
    # 0.5 GB worth of MB = 512 MB → best_unit picks "MB" (not divisible).
    # But 1536 MB / 1024 = 1.5 GB. best_unit needs `%`==0 → stays MB.
    assert format_human(1536, "quota") == "1536 MB"
