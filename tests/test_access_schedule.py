"""Tests for app.radius.core.access_schedule — see SERVICES_COOKBOOK §17."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.radius.core.access_schedule import (
    AccessScheduleError, DAYS,
    derive_working_days, empty_schedule, is_allowed, parse, serialize, validate,
)


# ───────────────────────── parse ────────────────────────────
def test_parse_none_and_empty_string_return_empty_windows():
    assert parse(None) == {"windows": []}
    assert parse("") == {"windows": []}


def test_parse_accepts_dict_and_json_string():
    raw_dict = {"windows": [{"days": ["sat", "mon"], "from": "01:00", "to": "03:00"}]}
    raw_json = '{"windows":[{"days":["sat","mon"],"from":"01:00","to":"03:00"}]}'
    assert parse(raw_dict) == parse(raw_json)


def test_parse_normalizes_day_order_to_canonical():
    # User submitted out-of-order, mixed case → canonical sat..fri order
    out = parse({"windows": [{"days": ["MON", "fri", "Sat"], "from": "", "to": ""}]})
    assert out["windows"][0]["days"] == ["sat", "mon", "fri"]


def test_parse_dedupes_days():
    out = parse({"windows": [{"days": ["sat", "sat", "sat"]}]})
    assert out["windows"][0]["days"] == ["sat"]


def test_parse_rejects_unknown_day():
    with pytest.raises(AccessScheduleError):
        parse({"windows": [{"days": ["funday"]}]})


def test_parse_rejects_bad_time():
    with pytest.raises(AccessScheduleError):
        parse({"windows": [{"days": ["sat"], "from": "25:00", "to": "03:00"}]})
    with pytest.raises(AccessScheduleError):
        parse({"windows": [{"days": ["sat"], "from": "1pm", "to": ""}]})


def test_parse_keeps_empty_time_strings():
    out = parse({"windows": [{"days": ["sat"], "from": "", "to": ""}]})
    assert out["windows"][0]["from"] == ""
    assert out["windows"][0]["to"]   == ""


def test_validate_is_alias_for_parse():
    assert validate({"windows": []}) == parse({"windows": []})


# ───────────────────────── serialize ────────────────────────
def test_serialize_roundtrip():
    sched = {"windows": [
        {"days": ["sat", "mon"], "from": "01:00", "to": "03:00"},
        {"days": ["thu"],         "from": "05:00", "to": "07:00"},
    ]}
    raw = serialize(sched)
    assert parse(raw) == sched


def test_serialize_empty_returns_empty_string():
    assert serialize({"windows": []}) == ""
    assert serialize(empty_schedule()) == ""


# ───────────────────────── is_allowed ───────────────────────
def test_empty_schedule_always_allows():
    # 2026-05-21 is a Thursday — pick any random moment
    assert is_allowed(None, datetime(2026, 5, 21, 14, 30)) is True
    assert is_allowed("",   datetime(2026, 5, 21, 14, 30)) is True
    assert is_allowed({"windows": []}, datetime(2026, 5, 21, 14, 30)) is True


def test_days_only_window_blocks_other_days():
    sched = {"windows": [{"days": ["sat"], "from": "", "to": ""}]}
    # 2026-05-23 = Saturday
    assert is_allowed(sched, datetime(2026, 5, 23, 14, 30)) is True
    # 2026-05-21 = Thursday — should be blocked
    assert is_allowed(sched, datetime(2026, 5, 21, 14, 30)) is False


def test_times_only_window_applies_to_all_days():
    sched = {"windows": [{"days": [], "from": "01:00", "to": "03:00"}]}
    # Thursday 01:30 → allowed
    assert is_allowed(sched, datetime(2026, 5, 21, 1, 30)) is True
    # Thursday 04:00 → outside
    assert is_allowed(sched, datetime(2026, 5, 21, 4, 0)) is False
    # Saturday 02:00 → also allowed (no day restriction)
    assert is_allowed(sched, datetime(2026, 5, 23, 2, 0)) is True


def test_days_and_times_window():
    sched = {"windows": [{"days": ["sat", "mon"], "from": "01:00", "to": "03:00"}]}
    # Saturday 01:30 ✓
    assert is_allowed(sched, datetime(2026, 5, 23, 1, 30)) is True
    # Saturday 04:00 ✗ outside time
    assert is_allowed(sched, datetime(2026, 5, 23, 4, 0)) is False
    # Thursday 01:30 ✗ outside day
    assert is_allowed(sched, datetime(2026, 5, 21, 1, 30)) is False


def test_per_day_advanced_windows():
    sched = {"windows": [
        {"days": ["sat"], "from": "01:00", "to": "04:00"},
        {"days": ["thu"], "from": "01:00", "to": "03:00"},
        {"days": ["thu"], "from": "05:00", "to": "07:00"},
    ]}
    # Saturday 02:00 ✓
    assert is_allowed(sched, datetime(2026, 5, 23, 2, 0)) is True
    # Thursday 02:00 ✓ (first thu window)
    assert is_allowed(sched, datetime(2026, 5, 21, 2, 0)) is True
    # Thursday 06:00 ✓ (second thu window)
    assert is_allowed(sched, datetime(2026, 5, 21, 6, 0)) is True
    # Thursday 04:00 ✗ between the two windows
    assert is_allowed(sched, datetime(2026, 5, 21, 4, 0)) is False
    # Friday — no window → blocked
    assert is_allowed(sched, datetime(2026, 5, 22, 2, 0)) is False


def test_window_inclusive_start_exclusive_end():
    sched = {"windows": [{"days": [], "from": "01:00", "to": "03:00"}]}
    assert is_allowed(sched, datetime(2026, 5, 21, 1, 0)) is True   # inclusive start
    assert is_allowed(sched, datetime(2026, 5, 21, 2, 59)) is True
    assert is_allowed(sched, datetime(2026, 5, 21, 3, 0)) is False  # exclusive end


def test_cross_midnight_window():
    # 22:00 → 02:00 means [22:00, 24:00) ∪ [00:00, 02:00)
    sched = {"windows": [{"days": [], "from": "22:00", "to": "02:00"}]}
    assert is_allowed(sched, datetime(2026, 5, 21, 23, 0)) is True
    assert is_allowed(sched, datetime(2026, 5, 21,  1, 0)) is True
    assert is_allowed(sched, datetime(2026, 5, 21,  3, 0)) is False


# ───────────────────────── derive_working_days ──────────────
def test_derive_working_days_empty_schedule():
    assert derive_working_days(None) == ""
    assert derive_working_days({"windows": []}) == ""


def test_derive_working_days_unrestricted_window_means_all_days():
    # A window with no `days` covers every day → CSV should be empty
    sched = {"windows": [{"days": [], "from": "01:00", "to": "03:00"}]}
    assert derive_working_days(sched) == ""


def test_derive_working_days_unions_all_referenced_days():
    sched = {"windows": [
        {"days": ["mon", "sat"], "from": "", "to": ""},
        {"days": ["thu"],         "from": "01:00", "to": "03:00"},
    ]}
    # Canonical order is sat,sun,mon,tue,wed,thu,fri
    assert derive_working_days(sched) == "sat,mon,thu"


def test_all_seven_days_in_canonical_order():
    sched = {"windows": [{"days": list(DAYS), "from": "", "to": ""}]}
    assert derive_working_days(sched) == ",".join(DAYS)
