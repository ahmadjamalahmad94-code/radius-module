"""device_health_poll_worker — end-to-end regression for the missing start call.

Bug history
───────────
The owner enabled «الفحص الدوري التلقائي» at /admin/radius/device-health, set
the interval to 1 minute, hit Save — but no check rows ever appeared.

Root cause (proved here): the function `start_device_health_poll_worker()`
was defined and re-exported from `app/workers/__init__.py`, but `app/__init__`'s
`_start_workers` never invoked it. So the background thread that drives the
`_poll_due → tick → insert_check` chain was never launched.

These tests prove:

  1. The startup hook now actually starts the worker thread. We inspect
     `app.workers.device_health_poll_worker._started` after `create_app()`.
  2. The save endpoint persists tenant settings to `tenant_settings`
     (device_health.poll_enabled / device_health.poll_minutes).
  3. With a monitored device seeded for tenant 1 and the settings set
     (enabled=True, minutes=1), `poll_once()` writes a row into
     `network_device_health_checks` whose `source='poller'`.
  4. `_poll_due` honors the minute interval: after a fresh check it's not
     due; after we backdate `last_check_at` past the window it is due
     again.

We exercise `poll_once()` directly (the loop body) instead of waiting 60s
for the real thread — same code path, deterministic.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture(scope="module")
def app():
    # Pin the worker enabled flag ON for the test process (default is on too,
    # but be explicit to insulate from env overrides on the runner).
    os.environ.pop("HOBERADIUS_DEVICE_HEALTH_POLL_WORKER_ENABLED", None)
    # Avoid auto-skip of workers — `_start_workers` bails when
    # PYTEST_CURRENT_TEST is set. We need it to RUN so the assertion is real.
    saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        from app import create_app
        a = create_app()
        yield a
    finally:
        if saved is not None:
            os.environ["PYTEST_CURRENT_TEST"] = saved


def test_worker_thread_actually_started(app):
    """After create_app(), the worker module's `_started` flag must be True
    (it flips inside `start_device_health_poll_worker`)."""
    from app.workers import device_health_poll_worker as w
    assert w._started is True, (
        "start_device_health_poll_worker() was not called in "
        "app/__init__.py:_start_workers — the worker thread is missing, "
        "so periodic health checks never run regardless of UI settings"
    )


def _seed_one_monitored_device(tenant_id: int = 1):
    """Insert one always-up dummy device + the matching nas row. We don't
    need real router IO — poller falls back to status='unknown' when the
    router is unreachable and STILL writes a check row via _log_check."""
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    # Ensure a NAS row exists so the join in poller doesn't crash.
    nas_row = db().execute(
        "SELECT id FROM nas_devices WHERE tenant_id=? LIMIT 1", (tenant_id,)
    ).fetchone()
    nas_id = nas_row["id"] if nas_row else None
    if nas_id is None:
        db().execute(
            "INSERT INTO nas_devices (tenant_id, name, address, secret, vendor, "
            "nas_type, enabled, created_at, connection_mode, api_user, api_password) "
            "VALUES (?, 'DH-Test', '10.0.0.99', 's', 'mikrotik', 'hotspot', 1, ?, "
            "'direct', 'u', 'p')",
            (tenant_id, now),
        )
        nas_id = db().execute(
            "SELECT id FROM nas_devices WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
            (tenant_id,)
        ).fetchone()["id"]
    # Remove any pre-existing test row from a prior failed run, then insert.
    db().execute(
        "DELETE FROM network_device_monitor_devices WHERE tenant_id=? AND name='dh-test-device'",
        (tenant_id,)
    )
    db().execute(
        "INSERT INTO network_device_monitor_devices(tenant_id, router_id, name, "
        "device_type, interface_name, ip_address, location, subnet_prefix, "
        "gateway_last_octet, ping_threshold_ms, netwatch_interval_sec, "
        "netwatch_timeout_sec, alert_channel, monitoring_enabled, status, "
        "created_at) VALUES (?, ?, 'dh-test-device', 'ap', 'ether2', "
        "'192.168.99.10', '', 24, 254, 80, 60, 3, '', 1, 'unknown', ?)",
        (tenant_id, nas_id, now),
    )


def test_save_endpoint_persists_settings(app):
    """POST /device-health/api/poll-settings writes both keys into tenant_settings."""
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["admin_user"] = "admin"
            sess["is_super_admin"] = True
            sess["tenant_id"] = 1
            sess["permissions"] = []
            sess["_csrf_token"] = "dh-csrf"
        res = c.post(
            "/admin/radius/device-health/api/poll-settings",
            json={"enabled": True, "minutes": 1},
            headers={"X-CSRFToken": "dh-csrf"},
        )
        assert res.status_code == 200, res.data
        data = res.get_json()
        assert data and data.get("ok") is True
        assert data["enabled"] is True
        assert data["minutes"] == 1

    from app.radius.db.repos import tenants_repo
    assert tenants_repo.get_setting(1, "device_health.poll_enabled")  in ("1", "true", "True")
    assert tenants_repo.get_setting(1, "device_health.poll_minutes")  == "1"


def test_poll_once_inserts_check_row_when_due(app):
    """With settings enabled + a monitored device, one `poll_once()` call
    writes a row into network_device_health_checks (source='poller')."""
    from app.workers.device_health_poll_worker import poll_once
    from app.radius.db.connection import db
    from app.radius.db.repos import tenants_repo

    _seed_one_monitored_device(1)
    tenants_repo.set_setting(1, "device_health.poll_enabled", "1")
    tenants_repo.set_setting(1, "device_health.poll_minutes", "1")

    # Wipe any prior poller rows so our assertion is unambiguous.
    db().execute(
        "DELETE FROM network_device_health_checks "
        "WHERE tenant_id=? AND source='poller'", (1,)
    )
    before = db().execute(
        "SELECT COUNT(*) AS c FROM network_device_health_checks "
        "WHERE tenant_id=? AND source='poller'", (1,)
    ).fetchone()["c"]
    assert before == 0

    stats = poll_once()
    assert stats["polled"] >= 1, (
        f"poll_once() reported nothing polled: {stats!r} — "
        "the loop body never reached tick() even though the device exists "
        "and settings are enabled"
    )
    after = db().execute(
        "SELECT COUNT(*) AS c FROM network_device_health_checks "
        "WHERE tenant_id=? AND source='poller'", (1,)
    ).fetchone()["c"]
    assert after == 1, (
        f"poll_once() returned polled={stats['polled']} but the checks "
        f"table grew by {after - before}, expected 1 — the periodic tick "
        "ran but the check row was NOT recorded"
    )


def test_poll_due_respects_one_minute_window(app):
    """Right after a recorded check, `_poll_due` is False. Backdate the
    timestamp past the window → True. Proves the schedule logic itself
    is not the bug."""
    from app.workers.device_health_poll_worker import _poll_due
    from app.radius.db.connection import db
    settings = {"enabled": True, "minutes": 1}

    # Fresh check just landed (from the previous test) → not due.
    assert _poll_due(1, settings) is False, (
        "_poll_due returned True immediately after a check landed — the "
        "interval gate is broken (would burn CPU on every 60s tick)"
    )

    # Backdate the latest poller row by 90 seconds → due again.
    past = (datetime.utcnow() - timedelta(seconds=90)).isoformat() + "Z"
    db().execute(
        "UPDATE network_device_health_checks SET created_at=? "
        "WHERE id=(SELECT MAX(id) FROM network_device_health_checks "
        "          WHERE tenant_id=? AND source='poller')",
        (past, 1),
    )
    assert _poll_due(1, settings) is True
