"""Fix wave 3 (F04 H1) — daily / monthly quotas across local midnight / month start.

A PPPoE session that started before local midnight (or the 1st of the month)
was left out of the day / month entirely, so a subscriber who stays connected
had an unlimited daily quota. Pins (panel zone Asia/Gaza, DST end 2026-10-24
02:00+03 → 01:00+02):

* a session started 22:35 that crosses midnight counts for the new day only
  from 00:00 (baseline interpolated between the last pre-midnight interim and
  the first one after it) — and the same at the 1st of the month;
* the minute sweep, authorize and the interim hook all see it;
* the HTTP accounting path folds Acct-*-Gigawords and snapshots the last
  pre-midnight reading before the first interim of the new day overwrites it;
* the daily TIME cap counts only today's share of a straddling session.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

MB = 1_048_576


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "quota3.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "fix3-quota-token")
    monkeypatch.setenv("FLASK_SECRET", "fix3-secret")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Gaza")
        yield application


@pytest.fixture(autouse=True)
def _no_pod(monkeypatch):
    calls = []
    import app.radius.services.policy_reconciler as pr
    monkeypatch.setattr(pr, "reconcile_active_sessions_against_policy",
                        lambda tid, **kw: calls.append((tid, kw)) or None)
    return calls


def _db():
    from app.radius.db.connection import db
    return db()


def _plan(**cols) -> int:
    now = datetime.utcnow().isoformat()
    fields = {"tenant_id": 1, "name": "q3_" + uuid4().hex[:6], "duration_minutes": 30 * 1440,
              "price": 30.0, "currency": "ILS", "speed_down_kbps": 4096,
              "speed_up_kbps": 1024, "enabled": 1, "created_at": now, "updated_at": now}
    fields.update(cols)
    cur = _db().execute(
        f"INSERT INTO access_plans({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
        tuple(fields.values()))
    return int(cur.lastrowid)


def _sub(plan_id: int):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = "q3_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Quota3", mobile="0599000000", status="enabled",
        expire_at=datetime.utcnow() + timedelta(days=400)))
    return subscribers_repo.get_subscriber(1, username)


def _session(username, *, start, upd, up_mb, down_mb, stop=None, sid=None) -> int:
    cur = _db().execute(
        "INSERT INTO radacct(tenant_id, username, acctsessionid, acctuniqueid, acctstarttime, "
        "acctupdatetime, acctstoptime, acctsessiontime, acctinputoctets, acctoutputoctets, "
        "nasipaddress) VALUES(1,?,?,?,?,?,?,60,?,?,'10.0.0.1')",
        (username, sid or ("s-" + uuid4().hex[:10]), uuid4().hex, start, upd, stop,
         int(up_mb * MB), int(down_mb * MB)))
    return int(cur.lastrowid)


def _interim(rid, *, upd, up_mb, down_mb, stop=None):
    _db().execute(
        "UPDATE radacct SET acctupdatetime=?, acctinputoctets=?, acctoutputoctets=?, "
        "acctstoptime=? WHERE radacctid=?",
        (upd, int(up_mb * MB), int(down_mb * MB), stop, rid))


def _mb(pair):
    return round((pair[0] + pair[1]) / MB, 3)


def _utc(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


# ─────────────── the owner's scenario: 22:35 → past midnight ───────────────

def test_session_started_22_35_counts_for_the_new_day_only_from_midnight(app):
    from app.radius.services import quota_period as qp
    from app.radius.db.repos import plans_repo
    pid = _plan(daily_combined_quota_mb=120)
    s = _sub(pid)
    # 2026-10-19 22:35 local (+03) = 19:35Z; local midnight 10-20 = 21:00Z.
    rid = _session(s.username, start="2026-10-19 19:35:00", upd="2026-10-19 20:50:00",
                   up_mb=25, down_mb=25)
    before = qp.usage(s, now=_utc("2026-10-19 20:55:00"))   # 23:55 local — same day
    assert _mb(before["daily"]) == 50.0
    # first interim after midnight: 00:10 local, 250 MB in total.
    _interim(rid, upd="2026-10-19 21:10:00", up_mb=125, down_mb=125)
    after = qp.usage(s, now=_utc("2026-10-19 21:15:00"))    # 00:15 local 10-20
    # linear split of the 23:50→00:10 interval: 150 MB at 00:00 → 100 MB today.
    assert _mb(after["daily"]) == 100.0
    assert after["daily"][0] == after["daily"][1] == 50 * MB
    # monthly: started this month → all of it.
    assert _mb(after["monthly"]) == 250.0
    plan = plans_repo.get_plan(1, pid)
    assert qp.window_exhaustion(s, plan, now=_utc("2026-10-19 21:15:00")) == ""
    # later interims keep the SAME baseline (fixed once per day).
    _interim(rid, upd="2026-10-19 21:40:00", up_mb=140, down_mb=140)
    later = qp.usage(s, now=_utc("2026-10-19 21:45:00"))
    assert _mb(later["daily"]) == 130.0
    assert qp.window_exhaustion(s, plan, now=_utc("2026-10-19 21:45:00")) == "daily"


def test_without_a_pre_midnight_snapshot_the_split_is_from_the_session_start(app):
    from app.radius.services import quota_period as qp
    s = _sub(_plan(daily_combined_quota_mb=10))
    # started 22:00 local (19:00Z), never read before midnight; interim 02:00
    # local (23:00Z) with 400 MB → 2 h before midnight, 2 h after → 200 MB today.
    _session(s.username, start="2026-10-19 19:00:00", upd="2026-10-19 23:00:00",
             up_mb=200, down_mb=200)
    u = qp.usage(s, now=_utc("2026-10-19 23:05:00"))
    assert _mb(u["daily"]) == 200.0


def test_dst_night_midnight_is_22z_after_the_switch(app):
    """10-25 00:00 local is +02 → 22:00Z (a fixed +3 offset would cut at 21:00Z)."""
    from app.radius.services import quota_period as qp
    s = _sub(_plan(daily_combined_quota_mb=500))
    rid = _session(s.username, start="2026-10-24 07:00:00", upd="2026-10-24 21:30:00",
                   up_mb=0, down_mb=100)
    qp.usage(s, now=_utc("2026-10-24 21:35:00"))            # 23:35 local, snapshot
    _interim(rid, upd="2026-10-24 22:30:00", up_mb=0, down_mb=300)
    u = qp.usage(s, now=_utc("2026-10-24 22:40:00"))         # 00:40 local 10-25
    # 21:30Z → 22:30Z, boundary 22:00Z halfway: base 200 → today 100 MB.
    assert _mb(u["daily"]) == 100.0
    assert u["daily"][0] == 0      # direction kept (all download)


def test_month_start_counts_only_from_the_first(app):
    from app.radius.services import quota_period as qp
    from app.radius.db.repos import plans_repo
    pid = _plan(monthly_combined_quota_mb=150)
    s = _sub(pid)
    # Nov 1 00:00 local (+02) = 2026-10-31 22:00Z.
    rid = _session(s.username, start="2026-10-31 18:00:00", upd="2026-10-31 21:00:00",
                   up_mb=50, down_mb=50)
    assert _mb(qp.usage(s, now=_utc("2026-10-31 21:10:00"))["monthly"]) == 100.0
    _interim(rid, upd="2026-10-31 23:00:00", up_mb=150, down_mb=150)
    u = qp.usage(s, now=_utc("2026-10-31 23:05:00"))
    assert _mb(u["monthly"]) == 100.0 and _mb(u["daily"]) == 100.0
    plan = plans_repo.get_plan(1, pid)
    assert qp.window_exhaustion(s, plan, now=_utc("2026-10-31 23:05:00")) == ""
    _interim(rid, upd="2026-11-01 01:00:00", up_mb=200, down_mb=200)
    assert qp.window_exhaustion(s, plan, now=_utc("2026-11-01 01:05:00")) == "monthly"


def test_session_that_stopped_after_midnight_counts_its_post_midnight_part(app):
    from app.radius.services import quota_period as qp
    s = _sub(_plan(daily_combined_quota_mb=1000))
    rid = _session(s.username, start="2026-10-19 19:35:00", upd="2026-10-19 20:50:00",
                   up_mb=10, down_mb=40)
    qp.usage(s, now=_utc("2026-10-19 20:55:00"))
    _interim(rid, upd="2026-10-19 21:10:00", up_mb=30, down_mb=120,
             stop="2026-10-19 21:10:00")
    u = qp.usage(s, now=_utc("2026-10-19 21:30:00"))
    # base at 21:00Z = (20, 80) → today (10, 40).
    assert u["daily"] == (10 * MB, 40 * MB)


def test_old_sessions_and_yesterday_sessions_do_not_count_today(app):
    from app.radius.services import quota_period as qp
    s = _sub(_plan(daily_combined_quota_mb=10))
    # closed yesterday; phantom open row whose last interim was yesterday.
    _session(s.username, start="2026-10-18 10:00:00", upd="2026-10-18 12:00:00",
             stop="2026-10-18 12:00:00", up_mb=500, down_mb=500)
    _session(s.username, start="2026-10-18 13:00:00", upd="2026-10-18 14:00:00",
             up_mb=700, down_mb=700)
    u = qp.usage(s, now=_utc("2026-10-19 10:00:00"))
    assert u["daily"] == (0, 0)


def test_daily_reset_still_wins_over_the_calendar_day(app):
    from app.radius.services import quota_period as qp
    s = _sub(_plan(daily_combined_quota_mb=100))
    now = datetime.utcnow()
    bounds = qp._local_bounds(1, now)
    ds = _utc(bounds["day_start"])
    rid = _session(s.username, start=(ds - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
                   upd=(ds + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
                   up_mb=100, down_mb=100)
    qp.reset_daily(s)                       # «استعادة الكوتة اليوميّة» now
    _interim(rid, upd=(ds + timedelta(seconds=2)).strftime("%Y-%m-%d %H:%M:%S"),
             up_mb=105, down_mb=105)
    assert _mb(qp.usage(s)["daily"]) == 10.0


# ─────────────── enforcement paths ───────────────

def _straddler_now(username, *, extra_gb=1.0):
    """A session opened 2 h before today's local midnight, read once at 23:59:59,
    then an interim 10 min after midnight carrying ``extra_gb`` more."""
    from app.radius.services import quota_period as qp
    bounds = qp._local_bounds(1)
    ds = _utc(bounds["day_start"])
    fmt = "%Y-%m-%d %H:%M:%S"
    rid = _session(username, start=(ds - timedelta(hours=2)).strftime(fmt),
                   upd=(ds - timedelta(seconds=1)).strftime(fmt), up_mb=5, down_mb=5)
    return rid, ds, fmt


def test_live_sweep_disconnects_a_straddling_session(app, _no_pod):
    from app.radius.services import quota_period as qp
    from app.radius.db.repos import subscribers_repo
    s = _sub(_plan(daily_combined_quota_mb=100))
    rid, ds, fmt = _straddler_now(s.username)
    sub = subscribers_repo.get_subscriber(1, s.username)
    qp.usage(sub)                                          # the 23:59:59 reading
    # the first interim of the new day (now — the sweep only sees live rows).
    now = max(datetime.utcnow(), ds + timedelta(seconds=2))
    _interim(rid, upd=now.strftime(fmt), up_mb=5, down_mb=1029)
    stats = qp.enforce_live_quota(1)
    assert stats["exhausted"] == 1
    assert _no_pod and _no_pod[-1][1]["usernames"] == [s.username]
    from app.radius.services.policy_engine import AuthRequest, authorize
    dec = authorize(AuthRequest(username=s.username, password="secret", tenant_id=1))
    assert not dec.ok and dec.reason == "quota_exhausted"


def test_marks_are_written_and_pruned(app):
    from app.radius.services import quota_period as qp
    s = _sub(_plan(daily_combined_quota_mb=100))
    rid, ds, fmt = _straddler_now(s.username)
    qp.usage(s)
    row = _db().execute("SELECT * FROM quota_session_marks WHERE radacctid=?", (rid,)).fetchone()
    assert row and row["snap_at"] == (ds - timedelta(seconds=1)).strftime(fmt)
    _db().execute("UPDATE quota_session_marks SET updated_at='2000-01-01 00:00:00'")
    assert qp.prune_session_marks() == 1


def test_http_interim_snapshots_before_overwrite_and_folds_gigawords(app):
    from app.radius.services import quota_period as qp
    from app.radius.services.accounting_events import AccountingEventsService
    s = _sub(_plan(daily_combined_quota_mb=100))
    bounds = qp._local_bounds(1)
    ds = _utc(bounds["day_start"])
    fmt = "%Y-%m-%d %H:%M:%S"
    rid = _session(s.username, start=(ds - timedelta(hours=3)).strftime(fmt),
                   upd=(ds - timedelta(minutes=5)).strftime(fmt), up_mb=7, down_mb=9,
                   sid="acct-http-1")
    AccountingEventsService().ingest(tenant_id=1, payload={
        "status_type": "Interim-Update", "username": s.username,
        "acct_session_id": "acct-http-1", "nas_ip_address": "10.0.0.1",
        "Acct-Input-Octets": 1000, "Acct-Input-Gigawords": 1,
        "Acct-Output-Octets": 2000, "Acct-Output-Gigawords": 2,
        "Acct-Session-Time": 11000})
    row = _db().execute("SELECT acctinputoctets, acctoutputoctets, acctupdatetime FROM radacct "
                        "WHERE radacctid=?", (rid,)).fetchone()
    assert row["acctinputoctets"] == 4294967296 + 1000
    assert row["acctoutputoctets"] == 2 * 4294967296 + 2000
    # the interim hook fixed today's baseline FROM the pre-midnight reading
    # (7 MB / 9 MB at 23:55 local), not from the session start at 0 bytes.
    mark = _db().execute("SELECT * FROM quota_session_marks WHERE radacctid=?", (rid,)).fetchone()
    assert mark["day_key"] == bounds["day_key"]
    expected = qp._interpolate(ds - timedelta(minutes=5), (7 * MB, 9 * MB),
                               _utc(qp._norm(row["acctupdatetime"])),
                               (row["acctinputoctets"], row["acctoutputoctets"]), ds)
    assert (mark["day_base_in"], mark["day_base_out"]) == expected
    from_start = qp._interpolate(ds - timedelta(hours=3), (0, 0),
                                 _utc(qp._norm(row["acctupdatetime"])),
                                 (row["acctinputoctets"], row["acctoutputoctets"]), ds)
    assert expected != from_start


def test_daily_time_cap_counts_only_todays_share_of_a_straddler(app):
    from app.radius.services.policy_engine import daily_used_seconds_bulk
    s = _sub(_plan())
    since = datetime.utcnow() - timedelta(hours=5)
    start = since - timedelta(hours=2)
    _db().execute(
        "INSERT INTO radacct(tenant_id, username, acctsessionid, acctuniqueid, acctstarttime, "
        "acctsessiontime, acctinputoctets, acctoutputoctets, nasipaddress) "
        "VALUES(1,?,?,?,?,?,0,0,'10.0.0.1')",
        (s.username, "t-" + uuid4().hex[:8], uuid4().hex,
         start.strftime("%Y-%m-%d %H:%M:%S"), 3 * 3600))
    got = daily_used_seconds_bulk(1, [s.username],
                                  since_iso=since.strftime("%Y-%m-%dT%H:%M:%S"))
    assert got[s.username] == 3600
