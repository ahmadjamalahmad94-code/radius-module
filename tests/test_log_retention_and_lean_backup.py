"""Tests for high-volume log retention + lean ("core") backups.

Covers the fix for the 188MB-backup bloat: the database grew unbounded because
the append-only log/accounting tables were never pruned and VACUUM never ran,
and the backup copied the whole DB. We verify:

  * run_retention() prunes OLD rows from log tables, keeps RECENT rows, never
    touches core business data, keeps OPEN radacct sessions, honours per-table
    env disable, and supports dry-run.
  * A lean backup empties the high-volume/BLOB tables but keeps core data and
    the full schema; a full archive keeps everything.
"""
from __future__ import annotations

import glob
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_retention_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]
    from app import create_app
    yield create_app()
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]


def _iso(days: int = 0) -> str:
    return (datetime.utcnow() + timedelta(days=days)).replace(microsecond=0).isoformat() + "Z"


def _seed_tenant():
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO tenants (id, slug, name, created_at) VALUES (1,'t1','T1',?)",
            (_iso(),),
        )


def _ins_event(created_at: str, *, category="system", key="k"):
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT INTO business_events (tenant_id, category, event_key, message, created_at) "
            "VALUES (1,?,?,?,?)",
            (category, key, "m", created_at),
        )


def _ins_radacct(*, start: str, stop: str | None):
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT INTO radacct (tenant_id, username, acctstarttime, acctstoptime) "
            "VALUES (1,'u',?,?)",
            (start, stop if stop is not None else ""),
        )


def _count(table: str) -> int:
    from app.radius.db.connection import db
    return int(db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _ins_snapshot(*, snapshot_type="license", status="active", payload="{}", created=None):
    from app.radius.db.connection import transaction
    created = created or _iso()
    with transaction() as c:
        c.execute(
            "INSERT INTO license_admin_bridge_snapshots "
            "(tenant_id, snapshot_type, normalized_status, source_url, payload_json, "
            " error_json, fetched_at, stale_after_seconds, created_at) "
            "VALUES (1,?,?,?,?,?,?,?,?)",
            (snapshot_type, status, "https://x", payload, "{}", created, 86400, created),
        )


def _snapshot_ids(snapshot_type="license") -> list[int]:
    from app.radius.db.connection import db
    rows = db().execute(
        "SELECT id FROM license_admin_bridge_snapshots WHERE snapshot_type=? ORDER BY id",
        (snapshot_type,),
    ).fetchall()
    return [int(r[0]) for r in rows]


# ── Retention ────────────────────────────────────────────────────────────


def test_prunes_old_keeps_recent(app):
    with app.app_context():
        _seed_tenant()
        _ins_event(_iso(-400))   # very old → pruned (business_events default 180d)
        _ins_event(_iso(-200))   # old → pruned
        _ins_event(_iso(-5))     # recent → kept
        assert _count("business_events") == 3

        from app.radius.services import log_retention
        res = log_retention.run_retention(actor="test")

        assert res["ok"] is True
        assert res["total_deleted"] == 2
        assert _count("business_events") == 1  # only the recent one remains
        assert res["vacuum_ran"] is True


def test_keeps_open_radacct_sessions(app):
    with app.app_context():
        _seed_tenant()
        _ins_radacct(start=_iso(-400), stop=_iso(-399))  # old, closed → pruned
        _ins_radacct(start=_iso(-400), stop=None)        # old but OPEN → kept
        _ins_radacct(start=_iso(-2), stop=_iso(-1))      # recent, closed → kept
        assert _count("radacct") == 3

        from app.radius.services import log_retention
        log_retention.run_retention(actor="test")

        # Open session survives even though its start is ancient.
        assert _count("radacct") == 2
        from app.radius.db.connection import db
        open_left = db().execute(
            "SELECT COUNT(*) FROM radacct WHERE acctstoptime=''"
        ).fetchone()[0]
        assert int(open_left) == 1


def test_core_data_untouched(app):
    with app.app_context():
        _seed_tenant()
        from app.radius.db.connection import transaction, db
        with transaction() as c:
            c.execute(
                "INSERT INTO tenant_settings (tenant_id, key, value, updated_at) "
                "VALUES (1,'brand','ACME',?)",
                (_iso(-400),),  # ancient timestamp — must NOT be pruned (core data)
            )
        _ins_event(_iso(-400))  # a log row that SHOULD be pruned

        from app.radius.services import log_retention
        log_retention.run_retention(actor="test")

        # Core setting kept despite ancient updated_at; the log row was pruned.
        assert int(db().execute("SELECT COUNT(*) FROM tenant_settings").fetchone()[0]) == 1
        assert _count("business_events") == 0
        assert int(db().execute("SELECT COUNT(*) FROM tenants").fetchone()[0]) == 1


def test_sweep_tables_pruned_by_age(app):
    """The schema-wide sweep: a representative newly-covered table
    (service_audit_log) is now age-pruned by the worker."""
    with app.app_context():
        _seed_tenant()
        from app.radius.db.connection import transaction
        with transaction() as c:
            for ts in (_iso(-400), _iso(-200), _iso(-5)):  # 180d window: 2 old, 1 recent
                c.execute(
                    "INSERT INTO service_audit_log "
                    "(tenant_id, entity_type, entity_id, action, actor, created_at) "
                    "VALUES (1,'svc','1','x','t',?)", (ts,))
        assert _count("service_audit_log") == 3

        from app.radius.services import log_retention
        res = log_retention.run_retention(actor="test")

        assert _count("service_audit_log") == 1  # only the recent row survives
        covered = {i["table"] for i in res["tables"]}
        # All 12 sweep tables are present in the retention pass.
        for t in ("service_audit_log", "router_lifecycle_events", "setup_wizard_runs",
                  "setup_wizard_router_snapshots", "network_policy_snapshots",
                  "mac_clone_events", "hotspot_card_sms_attempts",
                  "payment_service_apply_attempts", "mikrotik_import_logs",
                  "webhook_deliveries", "financial_report_snapshots", "price_snapshots"):
            assert t in covered


def test_env_disable_per_table(app, monkeypatch):
    with app.app_context():
        _seed_tenant()
        _ins_event(_iso(-400))
        monkeypatch.setenv("HOBERADIUS_RETENTION_BUSINESS_EVENTS_DAYS", "0")

        from app.radius.services import log_retention
        res = log_retention.run_retention(actor="test")

        assert _count("business_events") == 1  # disabled → not pruned
        skipped = {i["table"]: i.get("skipped") for i in res["tables"]}
        assert skipped.get("business_events") == "disabled"


def test_dry_run_deletes_nothing(app):
    with app.app_context():
        _seed_tenant()
        _ins_event(_iso(-400))

        from app.radius.services import log_retention
        res = log_retention.run_retention(actor="test", dry_run=True)

        assert res["dry_run"] is True
        assert res["total_deleted"] == 1   # would delete 1
        assert _count("business_events") == 1  # but nothing actually deleted
        assert res["vacuum_ran"] is False


# ── Bridge snapshot cache (keep-latest-per-scope) ─────────────────────────


def test_snapshot_keeps_latest_n_per_scope(app):
    with app.app_context():
        _seed_tenant()
        # 8 license + 3 capacity snapshots, all healthy.
        for _ in range(8):
            _ins_snapshot(snapshot_type="license", status="active")
        for _ in range(3):
            _ins_snapshot(snapshot_type="capacity_contract", status="valid")
        assert _count("license_admin_bridge_snapshots") == 11

        from app.radius.services import log_retention
        res = log_retention.run_retention(actor="test")

        # Default keep=5 per scope → license trimmed 8→5, capacity (3) untouched.
        lic = _snapshot_ids("license")
        cap = _snapshot_ids("capacity_contract")
        assert len(lic) == 5
        assert len(cap) == 3            # fewer than keep → all kept
        # The 5 kept are the most-recent (highest ids).
        assert lic == sorted(lic)[-5:]
        snap = {i["table"]: i for i in res["tables"]}["license_admin_bridge_snapshots"]
        assert snap["deleted"] == 3
        assert res["vacuum_ran"] is True


def test_snapshot_preserves_old_last_success(app):
    with app.app_context():
        _seed_tenant()
        # One ancient SUCCESS, then 7 recent FAILURES. keep=5 would by-id drop
        # the ancient success, but it must survive because it is the last
        # usable snapshot the dashboard reads via latest_success().
        _ins_snapshot(status="active", created=_iso(-400))   # id 1, old success
        for _ in range(7):
            _ins_snapshot(status="unavailable")              # ids 2..8, failures
        assert _count("license_admin_bridge_snapshots") == 8

        from app.radius.services import log_retention
        log_retention.run_retention(actor="test")

        ids = _snapshot_ids("license")
        # The old success (id 1) is preserved on top of the latest-5 failures.
        assert 1 in ids
        assert len(ids) == 6   # last-success + 5 most-recent failures

        # And the store still resolves it as the last success.
        from app.radius.services.admin_panel_client import LicenseAdminSnapshotStore
        success = LicenseAdminSnapshotStore().latest_success(
            tenant_id=1, snapshot_type="license")
        assert success is not None
        assert int(success["id"]) == 1


def test_snapshot_keep_env_disable(app, monkeypatch):
    with app.app_context():
        _seed_tenant()
        for _ in range(8):
            _ins_snapshot(status="active")
        monkeypatch.setenv(
            "HOBERADIUS_RETENTION_LICENSE_ADMIN_BRIDGE_SNAPSHOTS_KEEP", "0")

        from app.radius.services import log_retention
        res = log_retention.run_retention(actor="test")

        assert _count("license_admin_bridge_snapshots") == 8   # disabled → kept
        snap = {i["table"]: i for i in res["tables"]}["license_admin_bridge_snapshots"]
        assert snap.get("skipped") == "disabled"


# ── Lean backup ──────────────────────────────────────────────────────────


def _backup_files(app):
    backup_dir = os.path.join(os.path.dirname(os.environ["HOBERADIUS_DB_PATH"]), "backups")
    return sorted(glob.glob(os.path.join(backup_dir, "*.sqlite3")))


def _table_count_in(path: str, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_lean_backup_excludes_logs_keeps_core(app):
    with app.app_context():
        _seed_tenant()
        # core row + a pile of log rows
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO tenant_settings (tenant_id, key, value, updated_at) "
                "VALUES (1,'brand','ACME',?)", (_iso(),))
        for _ in range(50):
            _ins_event(_iso(-1))
            _ins_radacct(start=_iso(-1), stop=_iso(-1))
            _ins_snapshot(status="active")

        from app.radius.services.operations import get_operations_service
        res = get_operations_service().run_local_backup(tenant_id=1, actor="test", lean=True)
        assert res["verified"] is True

        files = _backup_files(app)
        assert files, "a backup file should have been produced"
        path = files[-1]
        # Excluded tables are emptied in the lean copy …
        assert _table_count_in(path, "business_events") == 0
        assert _table_count_in(path, "radacct") == 0
        assert _table_count_in(path, "license_admin_bridge_snapshots") == 0
        # … but the schema is intact and core data is preserved.
        assert _table_count_in(path, "tenant_settings") == 1
        assert _table_count_in(path, "tenants") == 1


def test_full_archive_includes_logs(app):
    with app.app_context():
        _seed_tenant()
        for _ in range(10):
            _ins_event(_iso(-1))

        from app.radius.services.operations import get_operations_service
        res = get_operations_service().run_local_backup(tenant_id=1, actor="test", lean=False)
        assert res["verified"] is True

        path = _backup_files(app)[-1]
        assert _table_count_in(path, "business_events") == 10  # full archive keeps logs
