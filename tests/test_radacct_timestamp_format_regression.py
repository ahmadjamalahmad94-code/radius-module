# -*- coding: utf-8 -*-
"""انحدار إنتاجيّ: صيغة طابع FreeRADIUS «مسافة» تَكسر مقارنة النافذة المعجمية.

الجذر (نفس بق device_limit المُصلَح في 1b29d07): أعمدة طوابع radacct
(acctupdatetime/acctstarttime/acctstoptime) كانت تُقارَن **كنصوص** ضدّ عتبة
ISO «…T…Z». في الإنتاج يَكتبها FreeRADIUS بصيغة SQL أصليّة
``YYYY-MM-DD HH:MM:SS`` (مسافة، بلا T/Z). بما أنّ المسافة (0x20) تَسبق ‎'T'
(0x54) معجميًّا، فكلّ صفّ إنتاجيّ «مسافة» يبدو **أقدم** من عتبة بنفس اليوم
بصيغة ISO → يُعامَل كزومبي/خارج النافذة → يُستبعَد → العدّ يُرجع 0 / يَنقُص.

هذه الاختبارات تُعيد إنتاج البق (تَفشل قبل الإصلاح) لكلّ المسارات المتأثّرة:
  • live_sessions: active_sessions_for_router / tenant_active_count /
    live_map / router_live  (عدّادات «المتصلون الآن» + المصدر الموثوق)
  • provider_grant.count_active_sessions + سقف «اكتف» (policy_engine)
  • connected_stats: «إحصائيات المتصلين» لليوم
  • policy_engine._accounted_session_seconds: سقف وقت الاتصال اليوميّ
  • accounting_events: Accounting-On debounce + mark_stale لا يُغلقان جلسة
    حيّة بصيغة «مسافة» خطأً.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_ts_fmt_")
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
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


# ─────────────── أدوات ───────────────

def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat() + "Z"


def _space(dt: _dt.datetime) -> str:
    """صيغة FreeRADIUS الإنتاجيّة: مسافة، بلا T/Z."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt(dt: _dt.datetime, fmt: str) -> str:
    return _space(dt) if fmt == "freeradius" else _iso(dt)


def _ensure_tenant(tenant_id: int) -> None:
    from app.radius.db.connection import db
    if db().execute("SELECT 1 FROM tenants WHERE id=?", (int(tenant_id),)).fetchone():
        return
    db().execute(
        "INSERT INTO tenants (id, slug, name, created_at) VALUES (?,?,?,?)",
        (int(tenant_id), f"t{int(tenant_id)}", f"Tenant {int(tenant_id)}",
         _iso(_dt.datetime.utcnow())))


def _add(tenant_id: int, username: str, *, ip: str = "203.0.113.9",
         fmt: str = "freeradius", age_min: int = 0, open: bool = True,
         session_id: str = "", sessiontime: int = 600,
         nasporttype: str = "Ethernet", proto: str = "") -> None:
    """يَضيف صفّ radacct بطابع زمنيّ بصيغة ``fmt`` («freeradius» مسافة / «iso»)."""
    from app.radius.db.connection import db
    _ensure_tenant(tenant_id)
    when = _dt.datetime.utcnow() - _dt.timedelta(minutes=age_min)
    ts = _fmt(when, fmt)
    db().execute(
        "INSERT INTO radacct (tenant_id, username, acctsessionid, nasipaddress, "
        " nasporttype, framedprotocol, framedipaddress, acctstarttime, "
        " acctupdatetime, acctstoptime, acctsessiontime) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (int(tenant_id), username, session_id or f"sess-{username}", ip,
         nasporttype, proto, "10.5.5.5", ts, ts,
         None if open else ts, int(sessiontime)))


def _seed_cap(tenant_id: int, payload: dict) -> None:
    from app.radius.db.connection import db
    now = _iso(_dt.datetime.utcnow())
    db().execute(
        "INSERT INTO license_admin_bridge_snapshots "
        "(tenant_id, snapshot_type, normalized_status, source_url, payload_json, "
        " error_json, fetched_at, stale_after_seconds, created_at) "
        "VALUES (?, 'capacity_contract', 'active', 'test://p', ?, '{}', ?, 86400, ?)",
        (int(tenant_id), json.dumps(payload, ensure_ascii=False), now, now))
    db().execute(
        "INSERT INTO license_admin_bridge_snapshots "
        "(tenant_id, snapshot_type, normalized_status, source_url, payload_json, "
        " error_json, fetched_at, stale_after_seconds, created_at) "
        "VALUES (?, 'license', 'active', 'test://l', ?, '{}', ?, 86400, ?)",
        (int(tenant_id), json.dumps({"status": "active"}), now, now))


def _mk_sub(username="newcomer", password="pw1", **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    base = dict(id=None, username=username, password=password, tenant_id=1,
                status="enabled")
    base.update(kw)
    return subscribers_repo.upsert_subscriber(Subscriber(**base))


# ═══════════════ (1) live_sessions — صيغة «مسافة» تُحتسب نشطة ═══════════════

def test_active_sessions_space_format_counts_as_active(app):
    """جوهر البق: جلسة بصيغة FreeRADIUS «مسافة» حيّة الآن تُحتسب نشطة.

    قبل الإصلاح: المقارنة المعجمية تَستبعدها (المسافة < 'T') فالعدّ 0."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _add(1, "ahmad", ip="203.0.113.9", fmt="freeradius")
        _add(1, "sara", ip="203.0.113.9", fmt="freeradius")
        r = ls.active_sessions_for_router(
            1, {"address": "203.0.113.9", "vpn_peer_address": ""})
        assert r["count"] == 2, "صفوف FreeRADIUS «مسافة» الحيّة يجب أن تُعَدّ"
        assert {s["username"] for s in r["sessions"]} == {"ahmad", "sara"}


def test_old_space_format_session_excluded_as_zombie(app):
    """جلسة «مسافة» قديمة (خارج النافذة) ما زالت تُستبعَد (لا تَنكسر النافذة)."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _add(1, "live", ip="203.0.113.9", fmt="freeradius", age_min=0)
        _add(1, "zombie", ip="203.0.113.9", fmt="freeradius", age_min=120)
        r = ls.active_sessions_for_router(
            1, {"address": "203.0.113.9", "vpn_peer_address": ""})
        assert r["count"] == 1
        assert r["sessions"][0]["username"] == "live"


def test_tenant_active_count_space_format(app):
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _add(1, "a", ip="203.0.113.9", fmt="freeradius")
        _add(1, "b", ip="10.10.0.5", fmt="freeradius", proto="PPP")
        _add(1, "old", ip="203.0.113.9", fmt="freeradius", age_min=120)  # زومبي
        assert ls.tenant_active_count(1) == 2


def test_live_map_and_router_live_space_format(app):
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _add(1, "a", ip="203.0.113.9", fmt="freeradius")
        _add(1, "b", ip="10.10.0.5", fmt="freeradius", proto="PPP")
        lmap = ls.live_map(1)
        assert ls.router_live(
            {"address": "203.0.113.9", "vpn_peer_address": ""}, lmap)["active"] == 1
        assert ls.router_live(
            {"address": "198.51.100.7", "vpn_peer_address": "10.10.0.5"},
            lmap)["online"]
        # last_seen مُطبَّع وقابل للمقارنة (راوتر حديث = online)
        assert ls.router_live(
            {"address": "203.0.113.9", "vpn_peer_address": ""},
            lmap)["online"]


def test_iso_format_still_works(app):
    """حارس عدم انحدار: صيغة ISO (مسار المحاسبة الداخليّ) ما زالت كما كانت."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _add(1, "live_iso", ip="203.0.113.9", fmt="iso", age_min=0)
        _add(1, "zombie_iso", ip="203.0.113.9", fmt="iso", age_min=120)
        r = ls.active_sessions_for_router(
            1, {"address": "203.0.113.9", "vpn_peer_address": ""})
        assert r["count"] == 1 and r["sessions"][0]["username"] == "live_iso"


# ═══════════════ (2) سقف «اكتف» — يَعدّ «مسافة» ويُنفِّذ ═══════════════

def test_provider_cap_counts_space_format_and_enforces(app):
    """السقف يَعدّ جلسات «مسافة» الحيّة ويَرفض الجديد عند البلوغ."""
    with app.app_context():
        from app.radius.services.provider_grant import count_active_sessions
        from app.radius.services.policy_engine import AuthRequest, authorize
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 1}}})
        _add(1, "other", ip="203.0.113.9", fmt="freeradius")  # حيّة «مسافة»
        assert count_active_sessions(1) == 1
        _mk_sub("newcomer", "pw1")
        d = authorize(AuthRequest(username="newcomer", password="pw1", tenant_id=1))
        assert not d.ok and d.reason == "provider_active_cap"


def test_provider_cap_space_format_zombie_does_not_block(app):
    """البق المُصلَح: جلسة «مسافة» يتيمة (قديمة) لا تَستهلك السقف فلا تَحجب دخولًا.

    قبل الإصلاح كان count_active_sessions يَعدّ كلّ ``acctstoptime IS NULL``
    بلا نافذة، فجلسة زومبي تَحجب مستخدمًا جديدًا للأبد."""
    with app.app_context():
        from app.radius.services.provider_grant import count_active_sessions
        from app.radius.services.policy_engine import AuthRequest, authorize
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 1}}})
        _add(1, "ghost", ip="203.0.113.9", fmt="freeradius", age_min=180)  # يتيمة
        assert count_active_sessions(1) == 0, "اليتيمة لا تُحتسَب ضدّ السقف"
        _mk_sub("newcomer", "pw1")
        d = authorize(AuthRequest(username="newcomer", password="pw1", tenant_id=1))
        assert d.ok, f"يجب السماح؛ السقف لا يُستهلَك بجلسة يتيمة: {d.reason}"


# ═══════════════ (3) connected_stats — إحصائيات اليوم ═══════════════

def test_connected_stats_today_counts_space_format(app):
    """«إحصائيات المتصلين» لليوم تَعدّ جلسات اليوم بصيغة «مسافة».

    قبل الإصلاح: حدود ISO «…T…Z» تَستبعد صفوف «مسافة» لنفس اليوم → 0."""
    with app.app_context():
        from app.radius.services import connected_stats as cs
        today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
        _add(1, "shop", ip="10.0.0.1", fmt="freeradius")
        _add(1, "shop", ip="10.0.0.1", fmt="freeradius")
        _add(1, "u2", ip="10.0.0.2", fmt="freeradius")
        s = cs.stats(1, mode="all", date_from=today, date_to=today)
        assert s["session_count"] == 3
        u = cs.stats(1, mode="unique", date_from=today, date_to=today)
        assert u["session_count"] == 2


# ═══════════════ (4) policy_engine — سقف الوقت اليوميّ ═══════════════

def test_accounted_seconds_counts_space_format(app):
    """مجموع acctsessiontime منذ بداية اليوم يَشمل جلسات «مسافة».

    قبل الإصلاح: الحصر المعجميّ ضدّ since_iso «…T…» يُسقط صفوف «مسافة» →
    العدّ 0 → السقف اليوميّ لا يُنفَّذ (وقت أكثر من المسموح)."""
    with app.app_context():
        from app.radius.services.policy_engine import _accounted_session_seconds
        today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
        since_iso = f"{today}T00:00:00"
        _add(1, "user1", fmt="freeradius", sessiontime=900)
        _add(1, "user1", fmt="freeradius", sessiontime=300)
        total = _accounted_session_seconds(1, "user1", since_iso)
        assert total == 1200


# ═══════════════ (5) accounting_events — لا يُغلق جلسة «مسافة» حيّة ═══════════

def _svc():
    from app.radius.services.accounting_events import AccountingEventsService
    return AccountingEventsService()


def test_phantom_accounting_on_preserves_space_format_session(app):
    """Accounting-On مرتدّ لا يَمسح جلسة «مسافة» بدأت للتوّ (debounce)."""
    with app.app_context():
        from app.radius.db.connection import db
        nas = "203.0.113.9"
        _add(1, "fresh", ip=nas, fmt="freeradius", session_id="S1")  # حيّة الآن
        res = _svc().ingest(tenant_id=1, payload={
            "status_type": "Accounting-On", "nas_ip_address": nas})
        assert res["preserved"] >= 1, "الجلسة «مسافة» الطازجة يجب أن تُحفَظ"
        row = db().execute(
            "SELECT acctstoptime FROM radacct WHERE acctsessionid='S1'").fetchone()
        assert not row["acctstoptime"], "يجب ألّا تُغلَق"


def test_mark_stale_keeps_recent_space_format_session(app):
    """mark_stale يُغلق القديمة فقط — لا يُغلق جلسة «مسافة» حديثة خطأً."""
    with app.app_context():
        from app.radius.db.connection import db
        _add(1, "recent", ip="203.0.113.9", fmt="freeradius",
             session_id="R1", age_min=0)
        _add(1, "ancient", ip="203.0.113.9", fmt="freeradius",
             session_id="A1", age_min=240)  # 4 ساعات
        out = _svc().mark_stale(tenant_id=1, older_than_seconds=3600)
        assert out["closed"] == 1, "القديمة فقط تُغلَق"
        recent = db().execute(
            "SELECT acctstoptime FROM radacct WHERE acctsessionid='R1'").fetchone()
        ancient = db().execute(
            "SELECT acctstoptime FROM radacct WHERE acctsessionid='A1'").fetchone()
        assert not recent["acctstoptime"], "الحديثة «مسافة» يجب أن تبقى حيّة"
        assert ancient["acctstoptime"], "القديمة يجب أن تُغلَق"
