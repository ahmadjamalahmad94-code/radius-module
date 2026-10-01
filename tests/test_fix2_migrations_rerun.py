"""Fix wave 2 integration: the three new migrations (178 request_hash,
179 subscriber_quota_state, 180 NAS live-name index) are unique, applied once,
and safe to run again — including 178's ``ALTER TABLE … ADD COLUMN``, which
SQLite cannot guard with IF NOT EXISTS (the runner skips a duplicate column).

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
import sqlite3

import pytest

NEW = ("178_api_idempotency_request_hash.sql",
       "179_subscriber_quota_state.sql",
       "180_nas_name_unique_live_only.sql")

# permguard/permmodel integration: permmodel's 186–189, renumbered 181–184.
PERM = ("181_admins_co_owner_authz_epoch.sql",
        "182_distributors_login_admin_id.sql",
        "183_repair_grants_d01_d02.sql",
        "184_role_scope_flags_to_rbac_keys.sql")


@pytest.fixture
def conn(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mig.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    from app.radius.db.connection import db, reset_for_tests
    reset_for_tests(db_file)
    from app.radius.db.migrations_runner import run_pending_migrations
    assert run_pending_migrations() > 0
    yield db()


def test_new_migration_numbers_are_unique():
    from app.radius.db.migrations_runner import list_migrations
    names = [p.name for p in list_migrations()]
    for name in NEW + PERM:
        assert name in names
        prefix = name[:4]
        assert [n for n in names if n.startswith(prefix)] == [name]


def test_second_run_applies_nothing(conn):
    from app.radius.db.migrations_runner import run_pending_migrations
    assert run_pending_migrations() == 0


def test_forgotten_new_migrations_rerun_cleanly(conn):
    """A DB that has the effects but lost the bookkeeping rows (a renumber whose
    old name was recorded, a restored backup) re-runs them without an error."""
    from app.radius.db.migrations_runner import run_pending_migrations
    conn.execute("DELETE FROM _migrations WHERE name IN (?,?,?)", NEW)
    assert run_pending_migrations() == 3
    cols = {r[1] for r in conn.execute("PRAGMA table_info(api_idempotency_keys)")}
    assert "request_hash" in cols
    idx = {r[1] for r in conn.execute("PRAGMA index_list(nas_devices)")}
    assert "idx_nas_unique_live" in idx and "idx_nas_unique" not in idx
    assert conn.execute("SELECT COUNT(*) FROM subscriber_quota_state").fetchone()[0] == 0
    assert run_pending_migrations() == 0


def test_runner_skips_only_duplicate_add_column(tmp_path):
    from app.radius.db.migrations_runner import _execute_migration
    c = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    c.execute("CREATE TABLE t (a INTEGER)")
    script = ("-- header comment\n"
              "ALTER TABLE t ADD COLUMN b TEXT;\n"
              "CREATE INDEX IF NOT EXISTS ix_t_b ON t (b);\n"
              "CREATE TRIGGER IF NOT EXISTS tr_t AFTER INSERT ON t BEGIN\n"
              "  UPDATE t SET b = 'x' WHERE a = NEW.a;\n"
              "END;\n")
    _execute_migration(c, "x.sql", script)
    _execute_migration(c, "x.sql", script)            # re-run: no «duplicate column»
    assert {r[1] for r in c.execute("PRAGMA table_info(t)")} == {"a", "b"}
    with pytest.raises(sqlite3.OperationalError):
        _execute_migration(c, "y.sql", "ALTER TABLE missing ADD COLUMN z TEXT;")


def test_permmodel_migrations_rerun_cleanly(conn):
    """181/182 are plain ``ADD COLUMN``s, 183/184 idempotent data repairs: a
    lost bookkeeping row re-runs all four without an error or a changed row."""
    from app.radius.db.migrations_runner import list_migrations, run_pending_migrations
    names = [p.name for p in list_migrations()]
    assert not [n for n in names if n[:3] in ("186", "187", "188", "189")]
    conn.execute("DELETE FROM _migrations WHERE name IN (?,?,?,?)", PERM)
    assert run_pending_migrations() == 4
    cols = {r[1] for r in conn.execute("PRAGMA table_info(admins)")}
    assert {"is_co_owner", "authz_epoch"} <= cols
    cols = {r[1] for r in conn.execute("PRAGMA table_info(distributors)")}
    assert "login_admin_id" in cols
    assert run_pending_migrations() == 0
