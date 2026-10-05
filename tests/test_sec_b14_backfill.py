"""B-14 migration plan — tools/sec_b14_backfill.py is dry-run by default,
moves only provable rows, keeps an undo log, and rolls back."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location(
        "sec_b14_backfill", ROOT / "tools" / "sec_b14_backfill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def dbfile(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "bf.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "bf")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield db_file


def _x(sql, args=()):
    from app.radius.db.connection import db
    return db().execute(sql, args)


def _seed_two_networks():
    _x("INSERT INTO tenants(id, slug, name, status, created_at) "
       "VALUES(2,'two','two','active','2026-01-01T00:00:00Z')")
    _x("INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, nas_type, "
       " enabled, created_at, vpn_peer_address) VALUES "
       "(1,'r1','203.0.113.10','s','mikrotik','hotspot',1,'2026-01-01T00:00:00Z','10.10.0.11'),"
       "(2,'r2','198.51.100.20','s','mikrotik','hotspot',1,'2026-03-01T00:00:00Z','10.10.0.22')")

    def acct(tid, sid, user, nas, start, stop=None):
        _x("INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
           " nasipaddress, acctstarttime, acctstoptime) VALUES(?,?,?,?,?,?,?)",
           (tid, sid, f"{nas}-{sid}", user, nas, start, stop))
    acct(1, "a1", "u1", "10.10.0.11", "2026-04-01 10:00:00", "2026-04-01 11:00:00")  # correct
    acct(1, "b1", "u2", "10.10.0.22", "2026-04-02 10:00:00", "2026-04-02 11:00:00")  # move
    acct(1, "b2", "u3", "10.10.0.22", "2026-04-03 10:00:00")                        # move (open)
    acct(1, "b3", "u4", "10.10.0.22", "2026-02-01 10:00:00", "2026-02-01 11:00:00")  # before r2
    acct(1, "b4", "u5", "10.10.0.22", "2026-04-04 10:00:00")                        # twin
    acct(2, "b4", "u5", "10.10.0.22", "2026-04-04 10:01:00")
    acct(1, "c1", "u6", "10.10.0.99", "2026-04-05 10:00:00")                        # unknown
    _x("INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, nas) VALUES "
       "(1,'p1','typo1','Access-Reject','2026-04-01T00:00:00Z','192.168.88.1'),"
       "(1,'p2','typo2','Access-Reject','2026-04-01T00:00:00Z','203.0.113.10')")


def _tenants_of(sid):
    return [r[0] for r in _x("SELECT tenant_id FROM radacct WHERE acctsessionid=? "
                             "ORDER BY radacctid", (sid,))]


def test_dry_run_changes_nothing_and_classifies(dbfile, capsys):
    _seed_two_networks()
    tool = _tool()
    assert tool.main(["--db", dbfile]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    v = out["radacct_tenant1_by_verdict"]
    assert v == {"correct": 1, "move": 2, "before_router_existed": 1,
                 "twin_in_target": 1, "unknown_nas": 1}
    assert out["rows_to_move_by_target_tenant"] == {"2": 2}
    assert out["radpostauth_tenant1_rows_with_foreign_nas_and_password"] == 1
    assert _tenants_of("b1") == [1] and _tenants_of("b2") == [1]
    assert "typo" not in json.dumps(out)        # never prints a password


def test_apply_moves_only_provable_rows_and_rollback_restores(dbfile, tmp_path, capsys):
    _seed_two_networks()
    tool = _tool()
    undo = str(tmp_path / "undo.json")
    assert tool.main(["--db", dbfile, "--apply", "--undo-file", undo,
                      "--redact-suspect-attempt-passwords"]) == 0
    assert _tenants_of("b1") == [2] and _tenants_of("b2") == [2]
    assert _tenants_of("a1") == [1] and _tenants_of("b3") == [1]
    assert _tenants_of("b4") == [1, 2] and _tenants_of("c1") == [1]
    passes = dict(_x("SELECT username, pass FROM radpostauth").fetchall())
    assert passes == {"p1": "***", "p2": "typo2"}
    # undo file is never overwritten
    with pytest.raises(FileExistsError):
        tool.main(["--db", dbfile, "--apply", "--undo-file", undo])
    # rollback: dry-run first, then real
    assert tool.main(["--db", dbfile, "--rollback", undo]) == 0
    assert _tenants_of("b1") == [2]
    assert tool.main(["--db", dbfile, "--rollback", undo, "--apply"]) == 0
    assert _tenants_of("b1") == [1] and _tenants_of("b2") == [1]


def test_single_tenant_server_is_a_noop(dbfile, tmp_path, capsys):
    _x("INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
       " acctstarttime) VALUES(1,'z','z','10.1.1.1','2026-04-01 00:00:00')")
    tool = _tool()
    assert tool.main(["--db", dbfile, "--apply", "--undo-file",
                      str(tmp_path / "u.json")]) == 0
    assert "single-tenant" in capsys.readouterr().out
    assert not (tmp_path / "u.json").exists()


def test_detection_sql_runs_read_only_and_selects_no_password(dbfile):
    import sqlite3
    _seed_two_networks()
    sql = (ROOT / "tools" / "sec_b14_detect.sql").read_text(encoding="utf-8")
    body = "\n".join(l for l in sql.splitlines() if not l.startswith("."))
    conn = sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    stmts = [s for s in body.split(";")
             if any(l.strip() and not l.strip().startswith("--") for l in s.splitlines())]
    assert len(stmts) >= 6
    for stmt in stmts:
        cols = [d[0] for d in conn.execute(stmt).description]
        assert "pass" not in cols
    conn.close()


def test_backfill_is_never_wired_into_the_app_or_migrations():
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "app").rglob("*.sql")):
        assert "sec_b14_backfill" not in path.read_text(encoding="utf-8", errors="ignore"), path
