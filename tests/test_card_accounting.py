"""Unit tests for card_accounting — the single source of truth for a card's
accounting mode + time budget + remaining time.

These are pure functions (no DB), so we exercise the whole decision matrix
here and lean on the checker/migration integration tests for wiring.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.radius.services.card_accounting import (
    MODE_BY_SECONDS,
    MODE_FROM_FIRST_CONNECT,
    accounting_mode,
    budget_seconds,
    first_connect_expiry,
    remaining_seconds,
    unit_to_seconds,
)


# ── unit_to_seconds ────────────────────────────────────────────────────
def test_unit_to_seconds_known_units():
    assert unit_to_seconds(3, "hours") == 3 * 3600
    assert unit_to_seconds(10, "minutes") == 10 * 60
    assert unit_to_seconds(2, "days") == 2 * 86400
    assert unit_to_seconds(1, "weeks") == 604800


def test_unit_to_seconds_unknown_unit_is_days():
    assert unit_to_seconds(1, "fortnight") == 86400


def test_unit_to_seconds_guards():
    assert unit_to_seconds(0, "hours") == 0
    assert unit_to_seconds(-5, "hours") == 0
    assert unit_to_seconds(None, "hours") == 0  # type: ignore[arg-type]


# ── accounting_mode ────────────────────────────────────────────────────
def test_accounting_mode_strings():
    assert accounting_mode(True) == MODE_FROM_FIRST_CONNECT
    assert accounting_mode(False) == MODE_BY_SECONDS


# ── budget_seconds precedence ──────────────────────────────────────────
def test_budget_prefers_validity_days_first():
    # validity_after_first_login_days wins over everything else.
    assert budget_seconds(
        validity_after_first_login_days=2,
        time_value=3, time_unit="hours",
        duration_minutes=99, validity_days=99,
    ) == 2 * 86400


def test_budget_uses_time_window_when_no_days():
    # «امواج البحر» = 3h expressed as a sub-day time window.
    assert budget_seconds(time_value=3, time_unit="hours") == 3 * 3600
    # «5 دقايق ابو العبد» = 10min.
    assert budget_seconds(time_value=10, time_unit="minutes") == 600


def test_budget_falls_back_to_plan_duration_then_validity():
    assert budget_seconds(duration_minutes=90) == 90 * 60
    assert budget_seconds(validity_days=7) == 7 * 86400


def test_budget_zero_when_nothing_set():
    assert budget_seconds() == 0


# ── remaining_seconds — MODE_FROM_FIRST_CONNECT ────────────────────────
def test_from_first_connect_counts_down_from_first_connection():
    # «امواج البحر»: 3h budget, first connect ~1h ago → ~2h remaining.
    now = datetime(2026, 7, 3, 16, 25, 0)
    first = datetime(2026, 7, 3, 15, 25, 0)  # 1h ago
    rem = remaining_seconds(
        mode=MODE_FROM_FIRST_CONNECT,
        budget=3 * 3600,
        now=now,
        first_connection_at=first,
    )
    assert rem == 2 * 3600


def test_from_first_connect_not_connected_yet_shows_full_budget():
    now = datetime(2026, 7, 3, 16, 25, 0)
    rem = remaining_seconds(
        mode=MODE_FROM_FIRST_CONNECT,
        budget=3 * 3600,
        now=now,
        first_connection_at=None,
    )
    assert rem == 3 * 3600


def test_from_first_connect_never_negative():
    now = datetime(2026, 7, 3, 20, 0, 0)
    first = datetime(2026, 7, 3, 15, 25, 0)  # 4.5h ago, budget only 3h
    rem = remaining_seconds(
        mode=MODE_FROM_FIRST_CONNECT,
        budget=3 * 3600,
        now=now,
        first_connection_at=first,
    )
    assert rem == 0


def test_from_first_connect_stale_expire_at_is_ignored_as_ceiling():
    # The migrated card carries a generation-time expire_at in the PAST.
    # It must NOT zero the countdown — that was the original bug.
    now = datetime(2026, 7, 3, 16, 25, 0)
    first = datetime(2026, 7, 3, 15, 25, 0)
    stale = datetime(2026, 1, 1, 0, 0, 0)  # long past
    rem = remaining_seconds(
        mode=MODE_FROM_FIRST_CONNECT,
        budget=3 * 3600,
        now=now,
        first_connection_at=first,
        expire_at=stale,
    )
    assert rem == 2 * 3600  # countdown wins, stale expiry ignored


# ── remaining_seconds — MODE_BY_SECONDS ────────────────────────────────
def test_by_seconds_burns_only_accounted_usage():
    rem = remaining_seconds(
        mode=MODE_BY_SECONDS,
        budget=3600,
        now=datetime(2026, 7, 3, 16, 0, 0),
        accounted_seconds=900,
    )
    assert rem == 2700


def test_by_seconds_never_negative():
    rem = remaining_seconds(
        mode=MODE_BY_SECONDS,
        budget=3600,
        now=datetime(2026, 7, 3, 16, 0, 0),
        accounted_seconds=99999,
    )
    assert rem == 0


# ── remaining_seconds — legacy expire_at fallback ──────────────────────
def test_legacy_calendar_card_uses_expire_at_when_no_budget():
    now = datetime(2026, 7, 3, 16, 0, 0)
    expiry = now + timedelta(hours=5)
    rem = remaining_seconds(
        mode=MODE_BY_SECONDS,
        budget=0,
        now=now,
        expire_at=expiry,
    )
    assert rem == 5 * 3600


def test_no_budget_no_expiry_is_none():
    rem = remaining_seconds(
        mode=MODE_FROM_FIRST_CONNECT,
        budget=0,
        now=datetime(2026, 7, 3, 16, 0, 0),
        first_connection_at=datetime(2026, 7, 3, 15, 0, 0),
    )
    assert rem is None


# ── first_connect_expiry ───────────────────────────────────────────────
def test_first_connect_expiry_adds_budget():
    first = datetime(2026, 7, 3, 15, 25, 0)
    assert first_connect_expiry(first, 3 * 3600) == datetime(2026, 7, 3, 18, 25, 0)


def test_first_connect_expiry_none_when_no_budget():
    assert first_connect_expiry(datetime(2026, 7, 3, 15, 25, 0), 0) is None
