"""Regression: `/system/backup/save` must run with a LONG socket timeout.

Live bug on ccr5: «خطأ في الاتصال: timed out» on `system/backup/save`. The tunnel
+ API were healthy — the cause was the snappy 3s default read timeout used for
dashboard reads. A CCR writes the binary backup to flash for several seconds, so
the read times out before `!done`. backup_save now bumps the per-operation
socket timeout (default 60s, `HOBERADIUS_MT_BACKUP_TIMEOUT_SEC`).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import socket
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.radius.services import mikrotik_admin_client as mac


@pytest.fixture(autouse=True)
def _reset_cache():
    mac._cache.clear()
    yield
    mac._cache.clear()


@pytest.fixture
def nas():
    return {
        "id": 1, "address": "203.0.113.5", "connection_mode": "direct",
        "api_port": 8728, "api_user": "admin", "api_password": "x",
        "api_use_tls": 0,
        # the offending default — short read timeout for snappy reads
        "api_timeout_sec": 3,
    }


class _RecordingClient:
    """Fake pooled client that records the override_timeout window and the
    command issued inside it."""

    def __init__(self):
        self.override_seconds = None
        self.timeout_active_during_run = None
        self.commands = []

    @contextmanager
    def override_timeout(self, seconds):
        self.override_seconds = seconds
        try:
            yield
        finally:
            pass

    def run(self, command, attrs=None, queries=None):
        # capture whether we're inside the override window
        self.timeout_active_during_run = self.override_seconds
        self.commands.append((command, attrs or {}))
        return []


def _pool(client):
    @contextmanager
    def fake_acquire(cfg):
        fake_acquire.last_cfg = cfg
        yield client
    return fake_acquire


def test_backup_save_runs_under_long_timeout(nas, monkeypatch):
    monkeypatch.delenv("HOBERADIUS_MT_BACKUP_TIMEOUT_SEC", raising=False)
    client = _RecordingClient()
    fake = _pool(client)
    with patch.object(mac, "_pool_acquire", fake):
        res = mac.backup_save(nas, name="nightly")
    assert res.ok is True
    # the /system/backup/save command ran INSIDE a >=60s override window
    assert client.commands == [("/system/backup/save", {"name": "nightly"})]
    assert client.override_seconds == 60
    assert client.timeout_active_during_run == 60
    # the pool cfg was opened at the bumped timeout (fresh-connection case)
    assert fake.last_cfg["timeout_sec"] == 60


def test_backup_timeout_is_env_tunable(nas, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_MT_BACKUP_TIMEOUT_SEC", "120")
    client = _RecordingClient()
    with patch.object(mac, "_pool_acquire", _pool(client)):
        mac.backup_save(nas, name="big")
    assert client.override_seconds == 120


def test_backup_timeout_has_a_floor(nas, monkeypatch):
    # a silly-small override can't drop us back to the snappy default
    monkeypatch.setenv("HOBERADIUS_MT_BACKUP_TIMEOUT_SEC", "1")
    client = _RecordingClient()
    with patch.object(mac, "_pool_acquire", _pool(client)):
        mac.backup_save(nas, name="x")
    assert client.override_seconds >= 15


def test_socket_timeout_maps_to_clear_message(nas):
    """A genuine socket timeout surfaces a clearer Arabic error, not the raw
    «خطأ في الاتصال: timed out»."""
    class _TimingOutClient:
        @contextmanager
        def override_timeout(self, seconds):
            yield
        def run(self, command, attrs=None, queries=None):
            raise socket.timeout("timed out")

    with patch.object(mac, "_pool_acquire", _pool(_TimingOutClient())):
        res = mac.backup_save(nas, name="x")
    assert res.ok is False
    assert "انتهت مهلة" in res.error
    assert "timed out" not in res.error


def test_short_op_keeps_default_timeout(nas):
    """A normal read (no op_timeout_sec) must NOT bump the cfg — the dashboard
    stays snappy on unreachable routers."""
    client = _RecordingClient()
    fake = _pool(client)
    with patch.object(mac, "_pool_acquire", fake):
        mac.interface_list(nas)
    # no override window entered for a plain read
    assert client.override_seconds is None
    assert fake.last_cfg["timeout_sec"] == 3


# ── the client-level override actually moves the live socket + restores it ──
def test_client_override_timeout_sets_and_restores_live_socket():
    from app.radius.integration.mikrotik.client import MikrotikClient
    c = MikrotikClient(host="10.0.0.1", username="u", password="p", timeout=3)
    sock = MagicMock()
    c._sock = sock
    assert c.timeout == 3
    with c.override_timeout(60):
        assert c.timeout == 60
        sock.settimeout.assert_called_with(60.0)
    # restored
    assert c.timeout == 3
    assert sock.settimeout.call_args_list[-1][0][0] == 3
