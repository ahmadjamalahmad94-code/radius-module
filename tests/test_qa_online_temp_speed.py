"""QA: live temporary-speed control from the online page.

Verifies the two gaps this feature closes:
  1. apply_temp_speed pushes a rate-CoA IMMEDIATELY (throttle on the live
     session now) and records the window + a restore snapshot.
  2. expire_due_temp_speeds / the worker push a REVERT CoA the moment the
     window ends and clear the flags — not lazily, not on next-auth.
Plus: a permanent custom override is restored exactly (never wiped), and the
window is a no-op before its end time.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_temp_speed_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


class _FakeCoa:
    def __init__(self, ok=True, code_name="ok"):
        self.ok = ok
        self.code_name = code_name


@pytest.fixture
def coa_calls(app, monkeypatch):
    """Capture every change_user_rate call and stub the wire send."""
    calls = []

    def _fake(tenant_id, username, *, new_rate_limit):
        calls.append({"tenant_id": tenant_id, "username": username,
                      "rate": new_rate_limit})
        return _FakeCoa(ok=True)

    import app.radius.integration.radius_coa as coa
    monkeypatch.setattr(coa, "change_user_rate", _fake)
    return calls


def _seed(app, *, custom=0, down=0, up=0, plan_up=10000, plan_down=5000):
    ts = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                         "VALUES (1,'t1','T1',?)", (ts,))
            conn.execute(
                "INSERT INTO access_plans(id, tenant_id, name, code, "
                "speed_up_kbps, speed_down_kbps, created_at) "
                "VALUES (901, 1, 'P', 'p', ?, ?, ?)", (plan_up, plan_down, ts))
            conn.execute(
                "INSERT INTO subscribers(tenant_id, username, password, plan_id, "
                "status, custom_speed, bandwidth_control_enabled, "
                "download_speed_kbps, upload_speed_kbps, created_at) "
                "VALUES (1, 'tmpuser', 'x', 901, 'enabled', ?, ?, ?, ?, ?)",
                (custom, 1 if custom else 0, down, up, ts))


def _row(app):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute(
            "SELECT temporary_speed, custom_speed, bandwidth_control_enabled, "
            "download_speed_kbps, upload_speed_kbps, metadata FROM subscribers "
            "WHERE username='tmpuser'").fetchone()


# ── 1. apply pushes a live CoA + records the window ──────────────────────────
def test_apply_pushes_live_coa_and_sets_window(app, coa_calls):
    _seed(app)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed
        res = apply_temp_speed(tenant_id=1, actor="t", username="tmpuser",
                               down_kbps=1024, up_kbps=1024, duration_minutes=5)
    assert res["rate"] == "1024k/1024k"
    assert res["coa"]["ok"] is True
    # a throttle CoA went out immediately with the chosen rate
    assert coa_calls and coa_calls[-1]["rate"] == "1024k/1024k"
    r = _row(app)
    assert r["temporary_speed"] == 1
    assert r["download_speed_kbps"] == 1024 and r["upload_speed_kbps"] == 1024
    assert r["bandwidth_control_enabled"] == 1
    import json
    meta = json.loads(r["metadata"])
    assert meta["temporary_speed_to"] and meta["temporary_speed_from"]
    # restore snapshot points at the plan rate (up/down)
    assert meta["temporary_speed_restore_rate"] == "10000k/5000k"


# ── 2. window is a no-op before it ends ──────────────────────────────────────
def test_expire_noop_before_due(app, coa_calls):
    _seed(app)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed, expire_due_temp_speeds
        t0 = datetime.utcnow()
        apply_temp_speed(tenant_id=1, actor="t", username="tmpuser",
                         down_kbps=1024, up_kbps=1024, duration_minutes=5, now=t0)
        n = expire_due_temp_speeds(tenant_id=1, now=t0 + timedelta(minutes=1))
    assert n == 0
    assert _row(app)["temporary_speed"] == 1


# ── 3. revert CoA fires exactly at expiry, temp-only cleared to plan ─────────
def test_expire_reverts_with_coa_after_due(app, coa_calls):
    _seed(app)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed, expire_due_temp_speeds
        t0 = datetime.utcnow()
        apply_temp_speed(tenant_id=1, actor="t", username="tmpuser",
                         down_kbps=1024, up_kbps=1024, duration_minutes=5, now=t0)
        coa_calls.clear()
        n = expire_due_temp_speeds(tenant_id=1, now=t0 + timedelta(minutes=6))
    assert n == 1
    # a REVERT CoA restored the plan rate immediately
    assert coa_calls and coa_calls[-1]["rate"] == "10000k/5000k"
    r = _row(app)
    assert r["temporary_speed"] == 0
    assert r["download_speed_kbps"] == 0 and r["upload_speed_kbps"] == 0
    assert r["bandwidth_control_enabled"] == 0


# ── 4. the worker's once-pass reverts an already-expired window ──────────────
def test_worker_expire_once_reverts(app, coa_calls):
    _seed(app)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed
        from app.workers.temp_speed_expiry_worker import expire_once
        # window opened 10 min ago for 5 min → already expired
        apply_temp_speed(tenant_id=1, actor="t", username="tmpuser",
                         down_kbps=1024, up_kbps=1024, duration_minutes=5,
                         now=datetime.utcnow() - timedelta(minutes=10))
        coa_calls.clear()
        out = expire_once()
    assert out["reverted"] == 1
    assert coa_calls and coa_calls[-1]["rate"] == "10000k/5000k"
    assert _row(app)["temporary_speed"] == 0


# ── 5. a permanent custom override is restored, never wiped ──────────────────
def test_permanent_override_restored_not_wiped(app, coa_calls):
    _seed(app, custom=1, down=8000, up=4000)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed, expire_due_temp_speeds
        t0 = datetime.utcnow()
        res = apply_temp_speed(tenant_id=1, actor="t", username="tmpuser",
                               down_kbps=1024, up_kbps=1024, duration_minutes=5, now=t0)
        # restore snapshot is the prior override (up/down), not the plan
        n = expire_due_temp_speeds(tenant_id=1, now=t0 + timedelta(minutes=6))
    assert res["rate"] == "1024k/1024k"
    assert n == 1
    assert coa_calls[-1]["rate"] == "4000k/8000k"     # restored to the override
    r = _row(app)
    assert r["temporary_speed"] == 0
    assert r["download_speed_kbps"] == 8000 and r["upload_speed_kbps"] == 4000
    assert r["bandwidth_control_enabled"] == 1         # override stays enabled


# ── 6. tenant isolation: another tenant's window is untouched ────────────────
def test_tenant_isolation_on_expiry(app, coa_calls):
    _seed(app)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed, expire_due_temp_speeds
        t0 = datetime.utcnow()
        apply_temp_speed(tenant_id=1, actor="t", username="tmpuser",
                         down_kbps=1024, up_kbps=1024, duration_minutes=5,
                         now=t0 - timedelta(minutes=10))
        # sweeping a DIFFERENT tenant must not revert tenant 1's window
        n = expire_due_temp_speeds(tenant_id=2, now=t0)
    assert n == 0
    assert _row(app)["temporary_speed"] == 1
