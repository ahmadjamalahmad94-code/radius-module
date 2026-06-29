"""Panel timezone — display filter + bandwidth-schedule evaluation in LOCAL tz.

The panel stores everything in UTC but the owner is in UTC+3. These tests pin
the `dt_local` filter (UTC → configured zone, DST-safe via zoneinfo) and prove
that bandwidth-schedule windows are evaluated against the owner's LOCAL wall
clock, not UTC. The default zone is Asia/Damascus (+3).
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
    db_file = os.path.join(tempfile.mkdtemp(), f"tz_{uuid4().hex}.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
    return app


def _set_tz(name: str) -> None:
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "billing.timezone", name)


# ─────────────────────────── display filter ───────────────────────────
def test_dt_local_default_zone_is_utc_plus_3(app):
    """The owner's complaint: a backup made at 08:40 local showed 05:40 (UTC).
    With the default Asia/Damascus zone a UTC 05:40 timestamp must read 08:40."""
    from app.radius.core.system_config import to_local
    with app.app_context():
        import flask
        flask.g.tenant_id = 1  # default tz = Asia/Damascus (+3)
        assert to_local("2026-06-29T05:40:00Z") == "2026-06-29 08:40"
        # naive (no Z) is treated as UTC too — no double conversion
        assert to_local("2026-06-29 05:40:00") == "2026-06-29 08:40"


def test_dt_local_utc_zone_is_identity(app):
    from app.radius.core.system_config import to_local
    with app.app_context():
        import flask
        flask.g.tenant_id = 1
        _set_tz("UTC")
        assert to_local("2026-06-29T05:40:00Z") == "2026-06-29 05:40"


def test_dt_local_is_dst_safe(app):
    """A zone WITH daylight saving must shift by the correct amount per date —
    proving zoneinfo (not a fixed offset) drives the conversion."""
    from app.radius.core.system_config import to_local
    with app.app_context():
        import flask
        flask.g.tenant_id = 1
        _set_tz("Europe/London")
        # London: +1 in summer (BST), +0 in winter (GMT) — same clock input.
        assert to_local("2026-06-29T05:40:00Z") == "2026-06-29 06:40"  # BST
        assert to_local("2026-01-15T05:40:00Z") == "2026-01-15 05:40"  # GMT


def test_dt_local_unknown_zone_falls_back_to_offset(app):
    from app.radius.core.system_config import to_local
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        import flask
        flask.g.tenant_id = 1
        _set_tz("Not/AZone")
        tenants_repo.set_setting(1, "billing.timezone_offset", "3")
        assert to_local("2026-06-29T05:40:00Z") == "2026-06-29 08:40"


def test_dt_local_filter_is_registered(app):
    with app.app_context():
        assert "dt_local" in app.jinja_env.filters
        assert "date_local" in app.jinja_env.filters


# ──────────────────── bandwidth-schedule local evaluation ────────────────────
def _mk_plan(app):
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import now_iso
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO access_plans(id,tenant_id,name,code,plan_type,"
            "service_type,duration_minutes,validity_days,speed_down_kbps,"
            "speed_up_kbps,price,currency,enabled,created_at) VALUES"
            "(1,1,'TZ Plan','TZ','time','PPPoE',1440,30,50000,25000,5,'JOD',1,?)",
            (now_iso(),),
        )


def _mk_schedule(app, *, start, end):
    from app.radius.services.operations import get_operations_service
    return get_operations_service().create_bandwidth_schedule(
        tenant_id=1, actor="t", data={
            "name": f"sch_{uuid4().hex[:6]}", "target_type": "plan",
            "plan_id": 1, "priority": 5, "starts_at_time": start,
            "ends_at_time": end, "speed_down_kbps": 80000, "speed_up_kbps": 40000,
            "restore_mode": "profile_default", "enabled": True})


def test_schedule_window_evaluated_in_local_tz(app):
    """Night window 00:00–06:00 in Asia/Damascus (+3). 22:00 UTC == 01:00 local
    → INSIDE; 05:00 UTC == 08:00 local → OUTSIDE. Under the old UTC logic the
    verdicts would be reversed, so this pins the local-tz behavior."""
    from app.radius.db.repos import operations_repo
    with app.app_context():
        _set_tz("Asia/Damascus")
        _mk_plan(app)
        _mk_schedule(app, start="00:00", end="06:00")

        inside = operations_repo.resolve_effective_bandwidth_schedule(
            1, plan_id=1, at=datetime(2026, 6, 29, 22, 0))   # 01:00 local
        assert inside is not None

        outside = operations_repo.resolve_effective_bandwidth_schedule(
            1, plan_id=1, at=datetime(2026, 6, 29, 5, 0))     # 08:00 local
        assert outside is None


def test_schedule_same_instant_differs_by_zone(app):
    """The SAME UTC instant resolves differently depending on the panel zone —
    direct evidence the evaluation honors the configured timezone."""
    from app.radius.db.repos import operations_repo
    with app.app_context():
        _mk_plan(app)
        _mk_schedule(app, start="00:00", end="06:00")
        at = datetime(2026, 6, 29, 22, 0)  # UTC

        _set_tz("UTC")          # 22:00 local → outside 00:00–06:00
        assert operations_repo.resolve_effective_bandwidth_schedule(
            1, plan_id=1, at=at) is None

        _set_tz("Asia/Damascus")  # 01:00 local → inside
        assert operations_repo.resolve_effective_bandwidth_schedule(
            1, plan_id=1, at=at) is not None
