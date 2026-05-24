"""S7 — Router snapshot cache + refresh service."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s7_")
    db_file = os.path.join(tmp, "test.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app
    yield create_app()
    reset_for_tests(None)


def _seed_nas(app, *, nas_id, enabled=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, 'direct', 'hr', 'p')""",
                (nas_id, f"s7-rtr-{nas_id}",
                 f"203.0.113.{nas_id}",
                 1 if enabled else 0, now),
            )


# ─── Repo ─────────────────────────────────────────────────────


def test_snapshots_table_exists(app):
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='router_snapshots'"
        ).fetchone()
        assert row is not None


def test_save_success_and_read_back(app):
    with app.app_context():
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_success(
            tenant_id=1, router_id=42,
            counters={"hotspot_active": 7},
            resource={"cpu-load": "12"},
        )
        snap = r.get_one(1, 42)
        assert snap is not None
        assert snap["counters"]["hotspot_active"] == 7
        assert snap["last_success_at"]
        assert snap["last_error"] == ""


def test_save_failure_keeps_last_data_but_records_error(app):
    with app.app_context():
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_success(tenant_id=1, router_id=42,
                       counters={"hotspot_active": 7})
        r.save_failure(tenant_id=1, router_id=42,
                       error="timeout reaching router")
        snap = r.get_one(1, 42)
        # Counters from the previous success survived.
        assert snap["counters"]["hotspot_active"] == 7
        # Error is recorded.
        assert "timeout" in snap["last_error"]


def test_save_failure_for_unknown_router_creates_row(app):
    """If the refresh fails before we ever had a success, the
    row still exists (so the UI can show "never reached")."""
    with app.app_context():
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_failure(tenant_id=1, router_id=99,
                       error="connect refused")
        snap = r.get_one(1, 99)
        assert snap is not None
        assert snap["last_success_at"] == ""
        assert "refused" in snap["last_error"]


def test_counters_redacts_secret_keys(app):
    with app.app_context():
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_success(tenant_id=1, router_id=50,
                       counters={"api_password": "leak"})
        snap = r.get_one(1, 50)
        assert snap["counters"]["api_password"] == "***"


def test_freshness_seconds_handles_missing_success(app):
    from app.radius.db.repos.router_snapshots_repo import (
        freshness_seconds,
    )
    assert freshness_seconds({}) >= 10 ** 8


def test_list_for_tenant(app):
    with app.app_context():
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_success(tenant_id=1, router_id=11, counters={})
        r.save_success(tenant_id=1, router_id=22, counters={})
        rows = r.list_for_tenant(1)
        assert {row["router_id"] for row in rows} == {11, 22}


# ─── Refresh service ──────────────────────────────────────────


def test_refresh_skips_disabled_router(app):
    _seed_nas(app, nas_id=80, enabled=False)
    with app.app_context():
        from app.radius.services.snapshot_refresh import refresh_one
        from app.radius.db.repos import router_snapshots_repo as r
        # Load row.
        from app.radius.db.connection import db
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE id=?", (80,),
        ).fetchone())
        out = refresh_one(1, row)
        assert out["ok"] is False
        assert out["reason"] == "disabled"
        snap = r.get_one(1, 80)
        assert snap is not None
        assert "معطّل" in snap["last_error"]


def test_refresh_one_calls_counters_and_resource(app, monkeypatch):
    _seed_nas(app, nas_id=81)
    # Stub the wire-level calls.
    from app.radius.services import (
        mikrotik_admin_client as mac, mt_counters,
    )
    from app.radius.services.mikrotik_admin_client import MtResult

    class _FakeCounters:
        def to_dict(self):
            return {"hotspot_active": 3, "ppp_active": 1}
    monkeypatch.setattr(mt_counters, "counters_for_nas",
                        lambda nas: _FakeCounters())
    monkeypatch.setattr(
        mac, "system_resource",
        lambda nas: MtResult(ok=True, data=[
            {"cpu-load": "5", "uptime": "1d"},
        ]),
    )
    with app.app_context():
        from app.radius.db.repos import router_snapshots_repo as r
        from app.radius.db.connection import db
        from app.radius.services.snapshot_refresh import refresh_one
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE id=?", (81,),
        ).fetchone())
        out = refresh_one(1, row)
        assert out["ok"] is True
        snap = r.get_one(1, 81)
        assert snap["counters"]["hotspot_active"] == 3
        assert snap["resource"]["cpu-load"] == "5"


def test_refresh_one_records_failure_on_exception(app, monkeypatch):
    _seed_nas(app, nas_id=82)
    from app.radius.services import mt_counters

    def _boom(nas):
        raise RuntimeError("connection timed out")
    monkeypatch.setattr(mt_counters, "counters_for_nas", _boom)
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import router_snapshots_repo as r
        from app.radius.services.snapshot_refresh import refresh_one
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE id=?", (82,),
        ).fetchone())
        out = refresh_one(1, row)
        assert out["ok"] is False
        snap = r.get_one(1, 82)
        assert "timed out" in snap["last_error"]


def test_refresh_fleet_aggregates_results(app, monkeypatch):
    _seed_nas(app, nas_id=90)
    _seed_nas(app, nas_id=91, enabled=False)
    from app.radius.services import (
        mikrotik_admin_client as mac, mt_counters,
    )
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(mt_counters, "counters_for_nas", lambda nas: {})
    monkeypatch.setattr(
        mac, "system_resource",
        lambda nas: MtResult(ok=True, data=[]),
    )
    with app.app_context():
        from app.radius.services.snapshot_refresh import refresh_fleet
        summary = refresh_fleet(1)
        assert summary["total"] == 2
        # 90 succeeds, 91 is disabled → counted as failed.
        assert summary["ok"] == 1
        assert summary["failed"] == 1
