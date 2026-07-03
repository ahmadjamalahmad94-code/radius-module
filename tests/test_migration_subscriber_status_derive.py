"""Unit tests for the subscriber status derivation used by the migration.

The «كله فعّال» bug: the migration set every subscriber to enabled because it
never derived 'expired' from a past expiry (and parse_status could not even
emit 'expired'). derive_status fixes that — pure, no DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.radius.services.migration.valueparse import derive_status, status_signal

NOW = datetime(2026, 7, 3, 12, 0, 0)
PAST = NOW - timedelta(days=10)
FUTURE = NOW + timedelta(days=10)


# ── status_signal (explicit source signal, distinguishes disabled/expired) ──
def test_signal_disabled_vs_expired():
    assert status_signal("disabled") == "disabled"
    assert status_signal("blocked") == "disabled"
    assert status_signal("معطّل") == "disabled"
    assert status_signal("expired") == "expired"
    assert status_signal("منتهي") == "expired"
    assert status_signal("active") == "enabled"
    assert status_signal("مفعّل") == "enabled"
    assert status_signal("") == ""
    assert status_signal("weird") == ""


# ── derive_status precedence ────────────────────────────────────────────
def test_past_expiry_derives_expired_even_if_source_says_active():
    # The crux: a source-active subscriber with a PAST expiry is expired.
    assert derive_status("active", expire_at=PAST, now=NOW) == "expired"


def test_future_expiry_stays_enabled():
    assert derive_status("active", expire_at=FUTURE, now=NOW) == "enabled"
    assert derive_status("", expire_at=FUTURE, now=NOW) == "enabled"


def test_explicit_disabled_beats_expiry():
    # A blocked user stays disabled even with a past expiry.
    assert derive_status("disabled", expire_at=PAST, now=NOW) == "disabled"
    assert derive_status("blocked", expire_at=FUTURE, now=NOW) == "disabled"


def test_no_signal_no_expiry_defaults_enabled():
    assert derive_status("", expire_at=None, now=NOW) == "enabled"


def test_explicit_expired_string():
    assert derive_status("expired", expire_at=None, now=NOW) == "expired"
