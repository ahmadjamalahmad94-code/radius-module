"""connected_live — الحالة الحيّة للراوتر هي مصدر «المتصلون الآن».

سياسة المالك:
  • راوتر غير قابل للوصول → عدّاد فارغ + إشارة «غير متصل»؛ لا تُعرَض جلسات
    RADIUS مفتوحة لا يمكن التحقّق منها.
  • راوتر قابل للوصول → يُعرَض عدده الحيّ.
  • لا سجلّ liveness إطلاقًا → ارتداد آمن إلى عدّ radacct (نشرة بلا API).
  • عند العودة → refresh_and_reconcile يَستطلع ويُصالح radacct فورًا.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_live_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_NAS_LIVENESS_WINDOW_SEC", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.services import nas_liveness
        nas_liveness.reset()
    yield a
    with a.app_context():
        from app.radius.services import nas_liveness
        nas_liveness.reset()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _isoz(dt):
    return dt.isoformat() + "Z"


def _seed_radacct(conn, *, username, nas, updated_min_ago=1, tenant_id=1):
    start = datetime.utcnow() - timedelta(minutes=10)
    upd = datetime.utcnow() - timedelta(minutes=updated_min_ago)
    conn.execute(
        "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, acctstarttime, acctupdatetime, acctstoptime) "
        "VALUES (?,?,?,?,?,?,?,NULL)",
        (tenant_id, f"s-{username}", f"u-{username}", username, nas,
         _isoz(start), _isoz(upd)),
    )


def _seed_nas(conn, *, nas_id, name, address, vpn=None, tenant_id=1):
    conn.execute(
        "INSERT INTO nas_devices (id, tenant_id, name, address, vpn_peer_address, "
        "enabled, created_at) VALUES (?,?,?,?,?,1,?)",
        (nas_id, tenant_id, name, address, vpn or "", _isoz(datetime.utcnow())),
    )


# ── 1. سجلّ liveness: ثلاث حالات + نافذة الحداثة ──────────────────────────

def test_liveness_states_and_window(app, monkeypatch):
    with app.app_context():
        from app.radius.services import nas_liveness as nl
        nl.reset()
        # unknown
        assert nl.is_reachable(1, "10.0.0.9") is None
        # reachable
        nl.record_reachable(1, "10.0.0.1", active_count=3)
        assert nl.is_reachable(1, "10.0.0.1") is True
        assert nl.live_connected_count(1) == 3
        # unreachable (most recent event is a failure)
        nl.record_unreachable(1, "10.0.0.1")
        assert nl.is_reachable(1, "10.0.0.1") is False
        assert nl.live_connected_count(1) == 0
        # stale success (older than window) → unreachable
        monkeypatch.setenv("HOBERADIUS_NAS_LIVENESS_WINDOW_SEC", "90")
        import time
        old = time.time() - 600
        nl.reset()
        nl.record_reachable(1, "10.0.0.2", active_count=5, now=old)
        assert nl.is_reachable(1, "10.0.0.2") is False  # بائت = غير متصل
        assert nl.live_connected_count(1) == 0


# ── 2. عدّاد: ارتداد radacct حين لا سجلّ liveness ─────────────────────────

def test_count_fallback_to_radacct_when_no_liveness(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import connected_live, nas_liveness
        nas_liveness.reset()
        with transaction() as c:
            _seed_radacct(c, username="u1", nas="10.10.0.2", updated_min_ago=1)
            _seed_radacct(c, username="u2", nas="10.10.0.2", updated_min_ago=1)
        info = connected_live.connected_count(1)
        assert info["source"] == "radacct"
        assert info["count"] == 2  # نافذة radacct (لا liveness)


# ── 3. راوتر متصل → العدّاد = العدد الحيّ ─────────────────────────────────

def test_reachable_router_uses_live_count(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import connected_live, nas_liveness
        nas_liveness.reset()
        with transaction() as c:
            _seed_nas(c, nas_id=1, name="Tower-A", address="10.10.0.2")
        nas_liveness.record_reachable(1, "10.10.0.2", active_count=4)
        info = connected_live.connected_count(1)
        assert info["source"] == "live"
        assert info["count"] == 4
        assert info["reachable"] is True
        assert info["unreachable_routers"] == []


# ── 4. راوتر غير متصل → عدّاد 0 + إشارة «غير متصل» ────────────────────────

def test_unreachable_router_zero_count_and_signal(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import connected_live, nas_liveness
        nas_liveness.reset()
        with transaction() as c:
            _seed_nas(c, nas_id=1, name="Tower-A", address="10.10.0.2")
            # جلسات radacct مفتوحة «شبحيّة» على الراوتر المنقطع — يجب ألّا تُعَدّ.
            _seed_radacct(c, username="ghost1", nas="10.10.0.2", updated_min_ago=1)
            _seed_radacct(c, username="ghost2", nas="10.10.0.2", updated_min_ago=1)
        nas_liveness.record_unreachable(1, "10.10.0.2")
        info = connected_live.connected_count(1)
        assert info["source"] == "live"
        assert info["count"] == 0  # لا متصلين يمكن التحقّق منهم
        assert info["reachable"] is False
        assert "Tower-A" in info["unreachable_routers"]


# ── 5. خريطة قابليّة الوصول تَنشر على العنوان العام + نفق WG ──────────────

def test_reachability_maps_public_and_tunnel_ip(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import connected_live, nas_liveness
        nas_liveness.reset()
        with transaction() as c:
            _seed_nas(c, nas_id=1, name="WG-Tower", address="1.2.3.4",
                      vpn="10.10.0.2")
        # liveness مُفتاحه عنوان الاستطلاع (address)
        nas_liveness.record_reachable(1, "1.2.3.4", active_count=1)
        reach = connected_live.reachability_by_ip(1)
        # كلا العنوانين (العام + النفق) يَحملان نفس الحالة
        assert reach.get("1.2.3.4") is True
        assert reach.get("10.10.0.2") is True


# ── 6. get_online_count + dashboard يتبعان الحالة الحيّة ──────────────────

def test_dashboard_online_count_follows_liveness(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import nas_liveness
        from app.radius.services.dashboard_metrics import get_online_count
        nas_liveness.reset()
        with transaction() as c:
            _seed_nas(c, nas_id=1, name="Tower-A", address="10.10.0.2")
            _seed_radacct(c, username="ghost", nas="10.10.0.2")
        # راوتر منقطع → 0 رغم وجود صفّ radacct مفتوح
        nas_liveness.record_unreachable(1, "10.10.0.2")
        assert get_online_count(1) == 0
        # عاد متصلاً بثلاث جلسات حيّة → 3
        nas_liveness.record_reachable(1, "10.10.0.2", active_count=3)
        assert get_online_count(1) == 3


# ── 7. مصالحة فور العودة: استطلاع ناجح يُسجّل الحياة ويُغلق اليتامى ────────

def test_refresh_and_reconcile_on_reconnect(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import connected_live, nas_liveness
        from app.workers import mt_reconciler
        nas_liveness.reset()

        with transaction() as c:
            _seed_nas(c, nas_id=1, name="Tower-A", address="10.10.0.2")
            # صفّ يتيم على الراوتر (سيختفي من المجموعة الحيّة → يُغلق)
            _seed_radacct(c, username="orphan", nas="10.10.0.2")

        # راوترٌ قابلٌ للوصول يردّ بجلسةٍ أخرى — «orphan» غائبٌ عنها حقًّا.
        # 🔑 لا نُحاكي مجموعةً **فارغة**: الفراغُ من راوترٍ استجاب عمًى لا
        #    إفادة، ولا يُغلق شيئًا (يُثبّته الاختبارُ التالي).
        monkeypatch.setattr(mt_reconciler, "_collect_router_configs",
                            lambda tid: [{"host": "10.10.0.2", "timeout_sec": 20}])
        monkeypatch.setattr(mt_reconciler, "_fetch_active_rows",
                            lambda cfg: [{"username": "someone-else",
                                          "mac": "AA:BB:CC:DD:EE:99"}])

        # الغيابُ يحتاج تمريرتين متتاليتين (الأولى تُؤجّل — قد تكون قراءةً ناقصة).
        connected_live.refresh_and_reconcile(1)
        stats = connected_live.refresh_and_reconcile(1)
        assert stats["reachable"] == 1
        assert nas_liveness.is_reachable(1, "10.10.0.2") is True
        # اليتيم أُغلق (غائب عن المجموعة الحيّة مرّتين على راوتر قابل للوصول)
        row = db().execute(
            "SELECT acctstoptime FROM radacct WHERE username='orphan'"
        ).fetchone()
        assert row["acctstoptime"] is not None


# ── 7b. 🔴 قراءةٌ فارغةٌ من مسار الصفحة لا تذبح — كانت تتسلّل من تحت الحارس ──

def test_refresh_empty_live_set_closes_nothing(app, monkeypatch):
    """`refresh_and_reconcile` يُنادي `_reconcile_nas` مباشرةً، فحارسُ
    «القائمة الفارغة» في `_reconcile_tenant` لم يكن يحميه: كلُّ فتحةِ
    صفحةٍ أثناء ارتجاجةِ نفقٍ كانت تذبح جلسات الـNAS كلَّها."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import connected_live, nas_liveness
        from app.workers import mt_reconciler
        nas_liveness.reset()

        with transaction() as c:
            _seed_nas(c, nas_id=1, name="Tower-A", address="10.10.0.2")
            _seed_radacct(c, username="alive1", nas="10.10.0.2")
            _seed_radacct(c, username="alive2", nas="10.10.0.2")

        monkeypatch.setattr(mt_reconciler, "_collect_router_configs",
                            lambda tid: [{"host": "10.10.0.2", "timeout_sec": 20}])
        monkeypatch.setattr(mt_reconciler, "_fetch_active_rows", lambda cfg: [])

        connected_live.refresh_and_reconcile(1)
        stats = connected_live.refresh_and_reconcile(1)

        assert stats["closed"] == 0, "قراءةٌ فارغةٌ من مسار الصفحة ذبحت جلسات"
        still_open = db().execute(
            "SELECT COUNT(*) AS c FROM radacct WHERE acctstoptime IS NULL"
        ).fetchone()["c"]
        assert still_open == 2


# ── 8. راوتر غير قابل للوصول في الاستطلاع → يُسجَّل منقطعًا، لا يُغلق شيئًا ──

def test_refresh_unreachable_records_down_no_close(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import connected_live, nas_liveness
        from app.workers import mt_reconciler
        nas_liveness.reset()

        with transaction() as c:
            _seed_nas(c, nas_id=1, name="Tower-A", address="10.10.0.2")
            _seed_radacct(c, username="alive", nas="10.10.0.2", updated_min_ago=1)

        monkeypatch.setattr(mt_reconciler, "_collect_router_configs",
                            lambda tid: [{"host": "10.10.0.2", "timeout_sec": 20}])
        monkeypatch.setattr(mt_reconciler, "_fetch_active_rows", lambda cfg: None)

        stats = connected_live.refresh_and_reconcile(1)
        assert stats["unreachable"] == 1
        assert nas_liveness.is_reachable(1, "10.10.0.2") is False
        # لم تُغلق أيّ جلسة (الراوتر غير قابل للوصول لا يُقتَل بناءً عليه)
        row = db().execute(
            "SELECT acctstoptime FROM radacct WHERE username='alive'"
        ).fetchone()
        assert row["acctstoptime"] is None
