"""QA: temporary-speed window is owned server-side (subscriber edit page).

Bugs fixed:
- countdown restarted to full duration on every refresh (window was rebuilt
  client-side) -> the server now keeps a still-valid window unchanged.
- could not set a new temp speed after the old one expired -> the server now
  recomputes the window when the stored end is missing/expired.
- could not cancel an active temp speed -> disabling clears the window.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.radius.routes.users import _parse_iso_naive, _resolve_temp_speed_window  # noqa: E402

NOW = datetime(2026, 6, 3, 12, 0, 0)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def test_active_window_is_kept_unchanged_on_resave():
    end = _iso(NOW + timedelta(minutes=25))
    fm = {
        "temporary_speed_from": _iso(NOW - timedelta(minutes=5)),
        "temporary_speed_to": end,
        "temporary_speed_duration_minutes": "30",
    }
    _resolve_temp_speed_window(fm, enabled=True, now=NOW)
    assert fm["temporary_speed_to"] == end          # NOT restarted


def test_expired_window_is_recomputed_so_new_one_can_be_set():
    fm = {
        "temporary_speed_to": _iso(NOW - timedelta(minutes=5)),   # already expired
        "temporary_speed_duration_minutes": "30",
    }
    _resolve_temp_speed_window(fm, enabled=True, now=NOW)
    assert _parse_iso_naive(fm["temporary_speed_to"]) == NOW + timedelta(minutes=30)
    assert _parse_iso_naive(fm["temporary_speed_from"]) == NOW


def test_fresh_enable_sets_window_from_duration():
    fm = {"temporary_speed_duration_minutes": "45"}
    _resolve_temp_speed_window(fm, enabled=True, now=NOW)
    assert _parse_iso_naive(fm["temporary_speed_from"]) == NOW
    assert _parse_iso_naive(fm["temporary_speed_to"]) == NOW + timedelta(minutes=45)


def test_disable_clears_window_cancel():
    fm = {
        "temporary_speed_from": _iso(NOW),
        "temporary_speed_to": _iso(NOW + timedelta(minutes=10)),
    }
    _resolve_temp_speed_window(fm, enabled=False, now=NOW)
    assert "temporary_speed_to" not in fm
    assert "temporary_speed_from" not in fm


def test_enable_without_duration_does_not_invent_a_window():
    fm = {}
    _resolve_temp_speed_window(fm, enabled=True, now=NOW)
    assert "temporary_speed_to" not in fm
    assert "temporary_speed_from" not in fm
