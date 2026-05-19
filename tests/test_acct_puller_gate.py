"""R3 gate: accounting_puller must not write to radacct by default.

FreeRADIUS rlm_sql_sqlite is the canonical writer after R1+R2. The puller
remains running for heartbeat/monitoring but its INSERTs/UPDATEs to
radacct are gated by HOBERADIUS_ACCT_PULLER_WRITES.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with the env var unset."""
    monkeypatch.delenv("HOBERADIUS_ACCT_PULLER_WRITES", raising=False)
    yield


def test_default_is_disabled():
    """No env var set → writes disabled. This is the R3 default."""
    from app.workers.accounting_puller import acct_puller_writes_enabled
    assert acct_puller_writes_enabled() is False


def test_explicit_zero_is_disabled(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ACCT_PULLER_WRITES", "0")
    from app.workers.accounting_puller import acct_puller_writes_enabled
    assert acct_puller_writes_enabled() is False


def test_explicit_false_is_disabled(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ACCT_PULLER_WRITES", "false")
    from app.workers.accounting_puller import acct_puller_writes_enabled
    assert acct_puller_writes_enabled() is False


def test_empty_string_is_disabled(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ACCT_PULLER_WRITES", "")
    from app.workers.accounting_puller import acct_puller_writes_enabled
    assert acct_puller_writes_enabled() is False


def test_garbage_value_is_disabled(monkeypatch):
    """Defensive: unrecognized strings do NOT enable — failure mode is
    "stay off" so we never accidentally re-enable double-writes."""
    monkeypatch.setenv("HOBERADIUS_ACCT_PULLER_WRITES", "maybe")
    from app.workers.accounting_puller import acct_puller_writes_enabled
    assert acct_puller_writes_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", "  1  "])
def test_explicit_truthy_re_enables(monkeypatch, val):
    """Emergency fallback: explicit truthy values re-enable legacy writes."""
    monkeypatch.setenv("HOBERADIUS_ACCT_PULLER_WRITES", val)
    from app.workers.accounting_puller import acct_puller_writes_enabled
    assert acct_puller_writes_enabled() is True
