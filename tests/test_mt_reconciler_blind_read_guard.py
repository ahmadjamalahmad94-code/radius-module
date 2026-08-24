"""mt_reconciler — راوترٌ يردّ قائمةً فارغةً أو ناقصةً لا يذبح الجلسات.

الخلفيّة الحيّة (فادي نت، خطٌّ متغيّر الـIP): نفقُ الإدارة يرتجّ، فيردّ
الراوترُ على `/ip/hotspot/active/print` بقائمةٍ **فارغة** — أو، بعد
الارتجاجة مباشرةً، بقائمةٍ **ناقصة** (٦٧ من ٨١) لأنّ جداولَه لم تمتلئ
بعد. المصالِحُ كان يقرأ الغيابَ إفادةً فيُغلق، فتظهر مئاتُ
`NAS-Lost-Session` يوميًّا وهي جلساتٌ حيّةٌ قتلها الخادمُ نفسُه.

التغطية:
  1. قائمةٌ فارغةٌ من راوترٍ يستجيب → لا إغلاق + `routers_blind`.
  2. قراءةٌ ناقصة (غيابٌ لأوّل مرّة) → لا إغلاق، تأجيلٌ تمريرةً واحدة.
  3. غيابٌ في تمريرتين متتاليتين → إغلاقٌ بسبب `NAS-Lost-Session`.
  4. عودةُ الجلسة في التمريرة الثانية تمسح «الغياب الأوّل» — فلا يتراكم
     الشكُّ عبر ارتجاجاتٍ متباعدة.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_blindread_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


NAS = "10.50.0.2"


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _seed(conn, *, username, mac, tenant_id=1):
    start = datetime.utcnow() - timedelta(minutes=30)
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
             callingstationid, acctstarttime, acctupdatetime, acctstoptime,
             acctinputoctets, acctoutputoctets)
        VALUES (?,?,?,?,?,?,?,?,NULL,0,0)
    """, (tenant_id, f"s-{username}", f"u-{username}", username, NAS, mac,
          _iso(start), _iso(datetime.utcnow() - timedelta(minutes=1))))


def _open_users(db, tenant_id=1) -> set[str]:
    rows = db().execute(
        "SELECT username FROM radacct WHERE tenant_id=? AND nasipaddress=? "
        "AND acctstoptime IS NULL", (tenant_id, NAS)).fetchall()
    return {r["username"] for r in rows}


def _row(user, mac):
    return {"username": user, "mac": mac}


# ── 1. قائمةٌ فارغةٌ من راوترٍ يستجيب = عمًى، لا «لا أحدَ متّصل» ──────────
def test_empty_read_from_reachable_router_closes_nothing(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers import mt_reconciler as mt

        with transaction() as c:
            _seed(c, username="a", mac="AA:BB:CC:00:00:01")
            _seed(c, username="b", mac="AA:BB:CC:00:00:02")

        monkeypatch.setattr(mt, "_all_tenants", lambda: [1])
        monkeypatch.setattr(mt, "_collect_router_configs",
                            lambda tid: [{"host": NAS}])
        monkeypatch.setattr(mt, "_fetch_active_rows", lambda cfg: [])

        stats = mt.reconcile_once()

        assert stats["closed_total"] == 0, "قائمةٌ فارغةٌ أغلقت جلسات"
        assert stats["routers_blind"] == 1
        assert _open_users(db) == {"a", "b"}


# ── 2. قراءةٌ ناقصة: غيابٌ لأوّل مرّة يُؤجَّل ولا يُغلق ────────────────────
def test_partial_read_defers_first_absence(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers import mt_reconciler as mt

        with transaction() as c:
            _seed(c, username="a", mac="AA:BB:CC:00:00:01")
            _seed(c, username="b", mac="AA:BB:CC:00:00:02")

        monkeypatch.setattr(mt, "_all_tenants", lambda: [1])
        monkeypatch.setattr(mt, "_collect_router_configs",
                            lambda tid: [{"host": NAS}])
        # الراوترُ ردّ بـ«a» فقط — «b» غابت لأوّل مرّة.
        monkeypatch.setattr(mt, "_fetch_active_rows",
                            lambda cfg: [_row("a", "AA:BB:CC:00:00:01")])

        stats = mt.reconcile_once()

        assert stats["closed_total"] == 0, "قراءةٌ ناقصةٌ ذبحت من أوّل مرّة"
        assert _open_users(db) == {"a", "b"}


# ── 3. الغيابُ المؤكَّد (تمريرتان) يُغلق ───────────────────────────────────
def test_second_consecutive_absence_closes(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers import mt_reconciler as mt

        with transaction() as c:
            _seed(c, username="a", mac="AA:BB:CC:00:00:01")
            _seed(c, username="b", mac="AA:BB:CC:00:00:02")

        monkeypatch.setattr(mt, "_all_tenants", lambda: [1])
        monkeypatch.setattr(mt, "_collect_router_configs",
                            lambda tid: [{"host": NAS}])
        monkeypatch.setattr(mt, "_fetch_active_rows",
                            lambda cfg: [_row("a", "AA:BB:CC:00:00:01")])

        mt.reconcile_once()                      # تمريرةٌ أولى: تأجيل
        stats = mt.reconcile_once()              # ثانيةٌ: إغلاق

        assert stats["closed_total"] == 1
        assert _open_users(db) == {"a"}
        cause = db().execute(
            "SELECT acctterminatecause AS c FROM radacct WHERE username='b'"
        ).fetchone()["c"]
        assert cause == "NAS-Lost-Session"


# ── 4. عودةُ الجلسة تمسح الشكّ — لا تراكمَ عبر ارتجاجاتٍ متباعدة ──────────
def test_reappearing_session_clears_suspicion(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers import mt_reconciler as mt

        with transaction() as c:
            _seed(c, username="a", mac="AA:BB:CC:00:00:01")
            _seed(c, username="b", mac="AA:BB:CC:00:00:02")

        monkeypatch.setattr(mt, "_all_tenants", lambda: [1])
        monkeypatch.setattr(mt, "_collect_router_configs",
                            lambda tid: [{"host": NAS}])

        full = [_row("a", "AA:BB:CC:00:00:01"), _row("b", "AA:BB:CC:00:00:02")]
        partial = [_row("a", "AA:BB:CC:00:00:01")]

        monkeypatch.setattr(mt, "_fetch_active_rows", lambda cfg: partial)
        mt.reconcile_once()                      # ارتجاجة: «b» غابت
        monkeypatch.setattr(mt, "_fetch_active_rows", lambda cfg: full)
        mt.reconcile_once()                      # عادت
        monkeypatch.setattr(mt, "_fetch_active_rows", lambda cfg: partial)
        stats = mt.reconcile_once()              # ارتجاجةٌ ثانيةٌ متباعدة

        assert stats["closed_total"] == 0, "الشكُّ تراكم عبر ارتجاجتين منفصلتين"
        assert _open_users(db) == {"a", "b"}


# ── 5. الراوترُ خلف نفقين: لا نُنشئ نسخةً ثانيةً لجلسةٍ لها صفٌّ مفتوح ─────
def test_materialize_does_not_duplicate_open_row_from_another_nas(app, monkeypatch):
    """فريراديوس حاسبَ الجلسةَ تحت نفقٍ (10.50.0.3) والمصالِحُ يستطلع
    الآخرَ (10.50.0.2). الجهازُ الواحد جلسةٌ واحدة — لا نسختان."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers import mt_reconciler as mt

        other_nas = "10.50.0.3"
        with transaction() as c:
            c.execute("""
                INSERT INTO radacct
                    (tenant_id, acctsessionid, acctuniqueid, username,
                     nasipaddress, callingstationid, acctstarttime,
                     acctupdatetime, acctstoptime, acctinputoctets,
                     acctoutputoctets)
                VALUES (1,'s-real','10.50.0.3-real','dual',?,?,?,?,NULL,0,0)
            """, (other_nas, "AA:BB:CC:00:00:09",
                  _iso(datetime.utcnow() - timedelta(minutes=5)),
                  _iso(datetime.utcnow())))

        monkeypatch.setattr(mt, "_all_tenants", lambda: [1])
        monkeypatch.setattr(mt, "_collect_router_configs",
                            lambda tid: [{"host": NAS}])
        monkeypatch.setattr(mt, "_fetch_active_rows",
                            lambda cfg: [_row("dual", "AA:BB:CC:00:00:09")])

        mt.reconcile_once()

        rows = db().execute(
            "SELECT nasipaddress, acctuniqueid FROM radacct "
            "WHERE username='dual' AND acctstoptime IS NULL").fetchall()
        assert len(rows) == 1, f"نسخةٌ مكرّرةٌ للجلسة نفسِها: {[dict(r) for r in rows]}"
        assert rows[0]["nasipaddress"] == other_nas
        assert not rows[0]["acctuniqueid"].startswith("mtsync:")
