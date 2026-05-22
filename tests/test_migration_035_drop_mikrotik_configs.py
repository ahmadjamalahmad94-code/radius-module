"""N3 — migration 035 drops mikrotik_configs after copying any
still-live rows into nas_devices.

The migration runs at app startup via the standard runner, so
once the test app boots we just inspect the resulting DB shape.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_n3_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_mikrotik_configs_table_is_dropped(app):
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='mikrotik_configs'"
        ).fetchone()
    assert row is None, "mikrotik_configs should be dropped by 035"


def test_nas_devices_still_present(app):
    """Sanity: 035 only drops the legacy table — the canonical
    nas_devices stays."""
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='nas_devices'"
        ).fetchone()
    assert row is not None


def test_mikrotik_repo_list_returns_empty_after_drop(app):
    """Workers that still import mikrotik_repo (accounting_puller,
    mt_reconciler, ...) must NOT crash. The repo wraps every
    'no such table' error and returns the empty equivalent."""
    with app.app_context():
        from app.radius.db.repos import mikrotik_repo
    assert mikrotik_repo.list_configs(1) == []
    assert mikrotik_repo.get(1, 1) is None
    assert mikrotik_repo.primary(1) is None
    # update / delete also no-op safely
    assert mikrotik_repo.update(1, 1, name="x") is None
    mikrotik_repo.delete(1, 1)   # must not raise


def test_migration_copies_pre_existing_rows(monkeypatch):
    """When migration 035 runs on a DB that DOES have a row in
    mikrotik_configs (e.g. an in-place upgrade of a live VPS),
    that row gets copied into nas_devices BEFORE the table is
    dropped. The copy lands disabled=0 so the operator must
    confirm credentials before HobeRadius dials it."""
    import os, sys, tempfile
    tmp = tempfile.mkdtemp(prefix="hr_n3_copy_")
    db_path = os.path.join(tmp, "test.db")

    # Bootstrap a DB through migrations 001-034 only (skip 035)
    # by manually applying them, planting a row, then running 035.
    # The simplest approach: spin up the full app TWICE — first
    # with a custom env hint to stop before 035, plant the row,
    # then re-create_app to apply 035. But our runner has no
    # such hint, so we go the direct route: bootstrap fully,
    # then re-seed mikrotik_configs by re-creating it (matches
    # the schema from migration 003 / older).
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_path)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    app = create_app()

    # By this point 035 has already dropped the table on first
    # boot. To test the copy logic, we recreate the table, plant
    # a row, and re-run the migration body inline.
    from app.radius.db.connection import db, transaction
    from datetime import datetime
    now = datetime.utcnow().isoformat() + "Z"
    migration_sql = open(
        "app/radius/db/migrations/035_drop_mikrotik_configs.sql",
        encoding="utf-8",
    ).read()
    with app.app_context():
        with transaction() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS mikrotik_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 8728,
                    username TEXT,
                    password TEXT,
                    use_tls INTEGER DEFAULT 0,
                    verify_tls INTEGER DEFAULT 1,
                    timeout_sec INTEGER DEFAULT 10,
                    enabled INTEGER DEFAULT 1,
                    last_status TEXT,
                    last_seen_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            c.execute("""
                INSERT INTO mikrotik_configs
                    (tenant_id, name, host, port, username, password,
                     use_tls, verify_tls, timeout_sec, enabled,
                     created_at, updated_at)
                VALUES (1, 'legacy-row', '192.0.2.99', 8728,
                        'admin', 'oldpass', 0, 1, 10, 1, ?, ?)
            """, (now, now))

        # Re-execute the migration body — use executescript so
        # SQLite handles multi-statement parsing the same way our
        # migrations runner does.
        from app.radius.db.connection import db
        conn = db()
        conn.executescript(migration_sql)
        conn.commit()

        # Assertion 1: the legacy table is gone again
        row = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='mikrotik_configs'"
        ).fetchone()
        assert row is None

        # Assertion 2: the row landed in nas_devices, disabled, marked
        nd = db().execute(
            "SELECT name, address, enabled, description, vendor "
            "FROM nas_devices WHERE address = '192.0.2.99'"
        ).fetchone()
    assert nd is not None, "row should have been copied into nas_devices"
    assert nd["name"] == "legacy-row"
    assert nd["enabled"] == 0, "migrated row must land disabled"
    assert "Migrated from mikrotik_configs" in (nd["description"] or "")
    assert nd["vendor"] == "mikrotik"
