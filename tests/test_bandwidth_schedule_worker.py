"""Feature 2 — auto-time schedule worker applies schedules LIVE by time window.

The worker is transition-based: it CoAs in-scope sessions only when a schedule
ENTERS its window (engage) or LEAVES it (release), so it is idempotent and never
spams CoA. ``tick_once(now=...)`` is deterministic for tests.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    # isolated DB per test: the worker counts GLOBAL schedule transitions, so a
    # DB shared across tests would leak schedules and break the exact counts.
    db_file = os.path.join(tempfile.mkdtemp(), f"wk_{uuid4().hex}.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso

        tenants_repo.ensure_default_tenant()
        # Window evaluation now runs in the tenant's configured LOCAL timezone.
        # Pin this tenant to UTC so these window-logic assertions stay
        # deterministic (now==local) regardless of the default tz. The new
        # local-tz behavior is covered in test_schedule_local_timezone.py.
        tenants_repo.set_setting(1, "billing.timezone", "UTC")
        with transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO access_plans(id,tenant_id,name,code,plan_type,"
                "service_type,duration_minutes,validity_days,speed_down_kbps,"
                "speed_up_kbps,price,currency,enabled,created_at) VALUES"
                "(1,1,'Wk Plan','WK','time','PPPoE',1440,30,50000,25000,5,'JOD',1,?)",
                (now_iso(),),
            )
    return app


def _mk_schedule(app, *, start, end, name=None):
    with app.app_context():
        from app.radius.services.operations import get_operations_service
        return get_operations_service().create_bandwidth_schedule(
            tenant_id=1, actor="t", data={
                "name": name or f"sch_{uuid4().hex[:6]}", "target_type": "plan",
                "plan_id": 1, "priority": 5, "starts_at_time": start,
                "ends_at_time": end, "speed_down_kbps": 80000, "speed_up_kbps": 40000,
                "restore_mode": "profile_default", "enabled": True})


def _reset():
    from app.workers.bandwidth_schedule_worker import reset_state_for_tests
    reset_state_for_tests()


# ───────────────────── engage → idempotent → release ─────────────────────
def test_worker_engages_then_idempotent_then_releases(app):
    from app.workers import bandwidth_schedule_worker as w

    _mk_schedule(app, start="00:00", end="06:00", name="night")
    with app.app_context():
        _reset()
        # 02:00 is inside the 00:00–06:00 window → ENGAGE
        s1 = w.tick_once(now=datetime(2026, 6, 28, 2, 0))
        assert s1["engaged"] == 1 and s1["released"] == 0
        # tick again still inside window → NO new transition (idempotent)
        s2 = w.tick_once(now=datetime(2026, 6, 28, 2, 30))
        assert s2["engaged"] == 0 and s2["released"] == 0
        # 12:00 is outside the window → RELEASE
        s3 = w.tick_once(now=datetime(2026, 6, 28, 12, 0))
        assert s3["released"] == 1 and s3["engaged"] == 0
        # still outside → idempotent
        s4 = w.tick_once(now=datetime(2026, 6, 28, 13, 0))
        assert s4["engaged"] == 0 and s4["released"] == 0


def test_worker_out_of_window_first_sight_is_noop(app):
    from app.workers import bandwidth_schedule_worker as w

    _mk_schedule(app, start="00:00", end="06:00")
    with app.app_context():
        _reset()
        # first ever sight is OUTSIDE the window → no engage, no release
        s = w.tick_once(now=datetime(2026, 6, 28, 9, 0))
        assert s["engaged"] == 0 and s["released"] == 0


def test_worker_disabled_schedule_never_engages(app):
    from app.workers import bandwidth_schedule_worker as w

    sched = _mk_schedule(app, start="00:00", end="06:00")
    with app.app_context():
        from app.radius.services.operations import get_operations_service
        get_operations_service().set_bandwidth_schedule_enabled(
            tenant_id=1, actor="t", schedule_id=sched["id"], enabled=False)
        _reset()
        s = w.tick_once(now=datetime(2026, 6, 28, 2, 0))
        assert s["engaged"] == 0


# ───────────────────── safe when live gate is OFF (dry) ─────────────────────
def test_worker_runs_dry_when_live_disabled(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", "0")
    from app.workers import bandwidth_schedule_worker as w

    _mk_schedule(app, start="00:00", end="06:00")
    with app.app_context():
        _reset()
        # transition still detected/counted; apply is dry (no CoA, no raise)
        s = w.tick_once(now=datetime(2026, 6, 28, 2, 0))
        assert s["engaged"] == 1


def test_worker_wraps_midnight_window(app):
    from app.workers import bandwidth_schedule_worker as w

    # 22:00 → 02:00 wraps midnight
    _mk_schedule(app, start="22:00", end="02:00", name="wrap")
    with app.app_context():
        _reset()
        assert w.tick_once(now=datetime(2026, 6, 28, 23, 0))["engaged"] == 1
        assert w.tick_once(now=datetime(2026, 6, 28, 1, 0))["engaged"] == 0   # still in
        assert w.tick_once(now=datetime(2026, 6, 28, 4, 0))["released"] == 1  # out
