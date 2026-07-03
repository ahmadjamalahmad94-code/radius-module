# -*- coding: utf-8 -*-
"""Live «connected now» panel must never disagree with its own session list.

Regression coverage for the phantom-open-row bug: the per-router card showed a
non-zero count (e.g. hotspot=1, total=1) while the list below said «no active
sessions». Root causes:

  * live_sessions._is_live() treated an open row with NO parseable
    acctstarttime/acctupdatetime as live (fail-safe True). A foreign radacct
    dump restored with acctstoptime IS NULL and NULL timestamps therefore
    inflated every «connected now» counter forever.
  * session_reconciler.reconcile_stale_interim() filtered on
    COALESCE(acctupdatetime, acctstarttime) < cutoff, which is NULL for such
    rows, so they could never be reaped.

The fix makes «genuinely active now» require positive evidence of recent life
(a parseable, in-window timestamp) — so the count and the list are derived from
the same predicate and always agree — and teaches the reaper to close the
timestamp-less phantoms so imported open sessions self-heal.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_live_phantom_")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "t.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    created = create_app()
    with created.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _now():
    return _dt.datetime.utcnow().isoformat() + "Z"


def _ago(minutes):
    return (_dt.datetime.utcnow() - _dt.timedelta(minutes=minutes)).isoformat() + "Z"


def _insert(username, ip, *, start=None, updated=None, stop=None,
            ptype="ethernet", proto="", sid=None):
    """Insert a radacct row. start/updated default to None (NULL) so callers can
    build phantom rows explicitly."""
    from app.radius.db.connection import db
    db().execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
        "nasporttype, framedprotocol, framedipaddress, acctstarttime, "
        "acctupdatetime, acctstoptime, acctsessiontime) "
        "VALUES(1,?,?,?,?,?,?,?,?,?,?)",
        (sid or (username + "-s"), username, ip, ptype, proto, "10.5.5.5",
         start, updated, stop, 0))


def _nas(address="203.0.113.9", vpn=""):
    return {"address": address, "vpn_peer_address": vpn}


# ───────────────────────── _is_live / per-router card ─────────────────────

def test_phantom_null_timestamps_not_counted_and_not_listed(app):
    """The exact reported symptom: a phantom open row (both timestamps NULL)
    must NOT count AND must NOT appear — count == len(sessions) == 0."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _insert("ghost", "203.0.113.9", start=None, updated=None)
        r = ls.active_sessions_for_router(1, _nas())
        assert r["count"] == 0
        assert r["hotspot"] == 0
        assert r["sessions"] == []


def test_empty_string_timestamps_not_counted(app):
    """Empty-string timestamps carry no life evidence either — also excluded."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _insert("blank", "203.0.113.9", start="", updated="")
        r = ls.active_sessions_for_router(1, _nas())
        assert r["count"] == 0 and r["sessions"] == []


def test_fresh_start_only_still_counts(app):
    """A just-authenticated session has acctstarttime set but no interim yet —
    it must still count (the fix keeps positive, in-window evidence alive)."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _insert("newbie", "203.0.113.9", start=_now(), updated=None)
        r = ls.active_sessions_for_router(1, _nas())
        assert r["count"] == 1 and r["sessions"][0]["username"] == "newbie"


def test_count_always_equals_list_length_mixed(app):
    """Consistency invariant with a mix of live + phantom + stale rows: the
    summary count must equal exactly what the list shows."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _insert("live1", "203.0.113.9", start=_now(), updated=_now())
        _insert("live2", "203.0.113.9", start=_ago(2), updated=_ago(1))
        _insert("phantom", "203.0.113.9", start=None, updated=None)   # excluded
        _insert("stale", "203.0.113.9", start=_ago(600), updated=_ago(600))  # old
        r = ls.active_sessions_for_router(1, _nas())
        assert r["count"] == len(r["sessions"]) == 2
        assert {s["username"] for s in r["sessions"]} == {"live1", "live2"}


def test_sort_does_not_crash_on_tied_timestamps(app):
    """Two rows sharing a timestamp key must not raise TypeError (dict compare)
    inside the sort — a latent crash that would blank the whole card."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        ts = _now()
        _insert("a", "203.0.113.9", start=ts, updated=ts, sid="a1")
        _insert("b", "203.0.113.9", start=ts, updated=ts, sid="b1")
        # Also two phantoms tied at datetime.max — same crash surface.
        _insert("p1", "203.0.113.9", start=None, updated=None, sid="p1")
        _insert("p2", "203.0.113.9", start=None, updated=None, sid="p2")
        r = ls.active_sessions_for_router(1, _nas())
        assert r["count"] == 2
        assert {s["username"] for s in r["sessions"]} == {"a", "b"}


# ───────────────────────── tenant_active_count / live_map ─────────────────

def test_tenant_active_count_excludes_phantom(app):
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _insert("live", "203.0.113.9", start=_now(), updated=_now())
        _insert("ghost", "203.0.113.9", start=None, updated=None)
        assert ls.tenant_active_count(1) == 1


def test_live_map_active_excludes_phantom(app):
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _insert("live", "203.0.113.9", start=_now(), updated=_now())
        _insert("ghost", "203.0.113.9", start=None, updated=None)
        lmap = ls.live_map(1)
        assert lmap.get("203.0.113.9", {}).get("active") == 1


# ───────────────────────── reaper closes phantoms ────────────────────────

def _open_count():
    from app.radius.db.connection import db
    return int(db().execute(
        "SELECT COUNT(*) AS c FROM radacct WHERE acctstoptime IS NULL"
    ).fetchone()["c"])


def test_reconcile_closes_null_timestamp_phantom(app):
    """The core reaper gap: a NULL-both open row was never reaped because
    COALESCE(...) < cutoff is NULL. It must now be closed on sight."""
    with app.app_context():
        from app.radius.services import session_reconciler as sr
        _insert("ghost", "203.0.113.9", start=None, updated=None)
        assert _open_count() == 1
        closed = sr.reconcile_stale_interim(tenant_id=1)
        assert closed == 1
        assert _open_count() == 0


def test_reconcile_leaves_fresh_open_and_closes_old(app):
    """Regression guard: fresh rows stay open; genuinely-old zombies still
    close. The phantom rule must not over-reap live sessions."""
    with app.app_context():
        from app.radius.services import session_reconciler as sr
        _insert("fresh", "203.0.113.9", start=_now(), updated=_now())
        _insert("old", "203.0.113.9", start=_ago(600), updated=_ago(600))
        closed = sr.reconcile_stale_interim(tenant_id=1)
        assert closed == 1                      # only «old»
        from app.radius.db.connection import db
        rows = {r["username"]: r["acctstoptime"] for r in db().execute(
            "SELECT username, acctstoptime FROM radacct WHERE tenant_id=1"
        ).fetchall()}
        assert rows["fresh"] is None            # still open
        assert rows["old"] is not None          # closed


def test_online_list_source_excludes_phantom(app):
    """The global /online list (list_online_from_radacct) must not surface a
    zero-evidence phantom either — a blank row there would disagree with the
    counters just as badly as on the per-router card."""
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services.sessions import get_online_sessions_service
        # «real» must own a subscribers row to qualify as connected (FIX A):
        # the /online list shows a session only if its username resolves to a
        # real subscriber/card, never a bare radacct row.
        db().execute(
            "INSERT INTO subscribers(tenant_id, username, password, created_at) "
            "VALUES (1,?,?,?)", ("real", "pw", _now()))
        _insert("real", "10.0.0.1", start=_now(), updated=_now())
        _insert("ghost", "10.0.0.1", start=None, updated=None)
        out = list(get_online_sessions_service().list(limit=50))
        names = sorted(s.username for s in out)
        assert names == ["real"]


def test_end_to_end_count_and_list_agree_after_reconcile(app):
    """The owner's scenario end-to-end: a phantom import row is neither counted
    nor listed (fix #1), and one reaper pass removes it for good (fix #2), so
    the counter and the list agree at 0 both before and after reconciliation."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        from app.radius.services import session_reconciler as sr
        _insert("imported-open", "203.0.113.9", start=None, updated=None)
        # Before reconcile: excluded from the live view already.
        r = ls.active_sessions_for_router(1, _nas())
        assert r["count"] == len(r["sessions"]) == 0
        assert ls.tenant_active_count(1) == 0
        # Reaper closes the phantom so it stops lingering as an open row.
        sr.reconcile_stale_interim(tenant_id=1)
        assert _open_count() == 0
        r2 = ls.active_sessions_for_router(1, _nas())
        assert r2["count"] == len(r2["sessions"]) == 0
