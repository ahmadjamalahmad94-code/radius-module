"""QA: bidirectional session sync helpers (mt_reconciler).

Piece 1: RouterOS uptime parsing + active-row mapping (pure functions).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.workers.mt_reconciler import (  # noqa: E402
    _keys_from_rows,
    _map_active_rows,
    _parse_ros_uptime,
)


def test_parse_uptime_unit_form():
    assert _parse_ros_uptime("22m54s") == 22 * 60 + 54
    assert _parse_ros_uptime("1h2m3s") == 3723
    assert _parse_ros_uptime("3h") == 10800
    assert _parse_ros_uptime("1w2d3h4m5s") == 604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert _parse_ros_uptime("") == 0
    assert _parse_ros_uptime("junk") == 0


def test_parse_uptime_colon_form():
    assert _parse_ros_uptime("00:22:54") == 22 * 60 + 54
    assert _parse_ros_uptime("1:02:03") == 3723


def test_map_hotspot_and_ppp_rows():
    hot = [{"user": "ahmad", "mac-address": "9e:49:36:50:27:a4",
            "address": "10.19.6.254", "uptime": "22m54s",
            "bytes-in": "1000", "bytes-out": "2000"}]
    ppp = [{"name": "pppuser", "caller-id": "AA:BB:CC:DD:EE:FF",
            "address": "10.20.0.5", "uptime": "1h"}]
    rows = _map_active_rows(hot, ppp)
    assert len(rows) == 2
    h = rows[0]
    assert h["username"] == "ahmad"
    assert h["mac"] == "9E:49:36:50:27:A4"        # normalized upper/colon
    assert h["framed_ip"] == "10.19.6.254"
    assert h["uptime_sec"] == 22 * 60 + 54
    assert h["bytes_in"] == 1000 and h["bytes_out"] == 2000
    assert h["source"] == "hotspot"
    p = rows[1]
    assert p["username"] == "pppuser" and p["mac"] == "AA:BB:CC:DD:EE:FF"
    assert p["source"] == "ppp" and p["framed_ip"] == "10.20.0.5"


def test_map_skips_rows_without_username():
    rows = _map_active_rows([{"mac-address": "aa:bb:cc:dd:ee:ff"}], [{"caller-id": "x"}])
    assert rows == []


def test_keys_from_rows_lowercases_user_uppercases_mac():
    rows = _map_active_rows(
        [{"user": "Ahmad", "mac-address": "9e:49:36:50:27:a4", "uptime": "1m"}], [])
    assert _keys_from_rows(rows) == {("ahmad", "9E:49:36:50:27:A4")}


# ── Piece 2: materialize router-observed sessions into radacct ──────────────
import pytest  # noqa: E402


@pytest.fixture
def app(monkeypatch, tmp_path):
    dbf = os.path.join(tmp_path, "sync.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", dbf)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(dbf)
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return a


def _db():
    from app.radius.db.connection import db
    return db()


def _hotspot(user="ahmad", mac="9e:49:36:50:27:a4", ip="10.19.6.254",
             uptime="5m", bin_="100", bout="200"):
    return _map_active_rows([{"user": user, "mac-address": mac, "address": ip,
                              "uptime": uptime, "bytes-in": bin_, "bytes-out": bout}], [])


def test_materialize_inserts_synthetic_for_cookie_session(app):
    from app.workers.mt_reconciler import _materialize_nas
    with app.app_context():
        res = _materialize_nas(1, "10.10.0.2", _hotspot())
        assert res["inserted"] == 1
        r = _db().execute(
            "SELECT * FROM radacct WHERE username='ahmad' AND acctstoptime IS NULL"
        ).fetchone()
        assert r is not None
        assert str(r["acctuniqueid"]).startswith("mtsync:")
        assert r["framedipaddress"] == "10.19.6.254"
        assert r["callingstationid"] == "9E:49:36:50:27:A4"
        assert r["nasipaddress"] == "10.10.0.2"


def test_materialize_does_not_dup_a_real_radacct_row(app):
    from app.workers.mt_reconciler import _materialize_nas
    with app.app_context():
        _db().execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
            "nasipaddress, acctstarttime, acctupdatetime, callingstationid, "
            "framedipaddress, acctinputoctets, acctoutputoctets, acctsessiontime) "
            "VALUES(1,'REAL-1','REAL-UNIQ','ahmad','10.10.0.2',datetime('now'),"
            "datetime('now'),'9E:49:36:50:27:A4','10.19.6.254',0,0,0)")
        res = _materialize_nas(1, "10.10.0.2", _hotspot())
        assert res["inserted"] == 0                # real row already covers it
        n = _db().execute("SELECT COUNT(*) c FROM radacct WHERE username='ahmad' "
                          "AND acctstoptime IS NULL").fetchone()["c"]
        assert n == 1                              # untouched, no duplicate


def test_materialize_refreshes_its_own_synthetic_row(app):
    from app.workers.mt_reconciler import _materialize_nas
    with app.app_context():
        _materialize_nas(1, "10.10.0.2", _hotspot(bin_="100", bout="200"))
        res = _materialize_nas(1, "10.10.0.2", _hotspot(uptime="6m", bin_="999", bout="888"))
        assert res["inserted"] == 0 and res["updated"] == 1
        rows = _db().execute("SELECT * FROM radacct WHERE username='ahmad' "
                             "AND acctstoptime IS NULL").fetchall()
        assert len(rows) == 1 and rows[0]["acctinputoctets"] == 999


def test_materialize_disabled_by_env(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SESSION_SYNC_MATERIALIZE", "0")
    from app.workers.mt_reconciler import _materialize_nas
    with app.app_context():
        assert _materialize_nas(1, "10.10.0.2", _hotspot())["inserted"] == 0
        assert _db().execute("SELECT COUNT(*) c FROM radacct").fetchone()["c"] == 0


def test_vanished_synthetic_is_closed_by_orphan_pass(app):
    from app.workers.mt_reconciler import _materialize_nas, _reconcile_nas
    with app.app_context():
        _materialize_nas(1, "10.10.0.2", _hotspot())
        closed = _reconcile_nas(1, "10.10.0.2", set())   # router now shows nothing
        assert closed == 1
        r = _db().execute("SELECT acctstoptime FROM radacct WHERE username='ahmad'").fetchone()
        assert r["acctstoptime"] is not None
