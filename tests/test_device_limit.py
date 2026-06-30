"""اختبارات «عدد الأجهزة المسموحة» (device_limit) + «الإغلاق الإجباري».

يُغطّي:
  • device_count يَتجاوز plan.concurrent_sessions ويُنفَّذ فعلاً عند المصادقة.
  • وضع reject → Access-Reject + رسالة «بلغت الحد الأقصى…».
  • وضع replace → فصل أقدم جلسة (إغلاقها في radacct) ثمّ السماح بالجديدة.
  • إعادة مصادقة نفس الجهاز (نفس MAC) لا تُحتسَب كجهازٍ ثانٍ.
  • الجلسات الزومبي (خارج نافذة الحياة) لا تُحتسَب فلا تَحجب دخولًا شرعيًّا.
  • session_reconciler.force_close يَكتب acctstoptime عبر المسار القانوني.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_app():
    tmp = tempfile.mkdtemp(prefix="hr_devlimit_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _ensure_tenant(tenant_id: int) -> None:
    """يَضمن وجود سجلّ tenant — FK في radacct يَتطلّبه."""
    from app.radius.db.connection import db
    if db().execute("SELECT 1 FROM tenants WHERE id=?",
                    (int(tenant_id),)).fetchone():
        return
    db().execute(
        "INSERT INTO tenants (id, slug, name, created_at) VALUES (?,?,?,?)",
        (int(tenant_id), f"t{int(tenant_id)}", f"Tenant {int(tenant_id)}",
         _iso(datetime.utcnow())))


def _add_session(tenant_id: int, username: str, *, mac: str = "",
                 session_id: str = "", nas_ip: str = "10.0.0.1",
                 age_min: int = 0, open: bool = True,
                 fmt: str = "iso") -> None:
    """يَضيف صفّ radacct (مفتوح افتراضًا) بطابع زمنيّ حيّ/قديم.

    ``fmt`` يُحدّد صيغة الطابع الزمنيّ المخزَّنة:
      • ``"iso"``        → ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (مسار المحاسبة الداخليّ)
      • ``"freeradius"`` → ``YYYY-MM-DD HH:MM:SS`` (FreeRADIUS الإنتاجيّ، مسافة)
    """
    from app.radius.db.connection import db
    _ensure_tenant(tenant_id)
    when = datetime.utcnow() - timedelta(minutes=age_min)
    if fmt == "freeradius":
        ts = when.strftime("%Y-%m-%d %H:%M:%S")
    else:
        ts = _iso(when)
    db().execute(
        "INSERT INTO radacct (tenant_id, username, acctsessionid, callingstationid, "
        " nasipaddress, acctstarttime, acctupdatetime, acctstoptime) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (int(tenant_id), username, session_id or f"sess-{username}-{mac}",
         mac, nas_ip, ts, ts, None if open else ts),
    )


def _mk_sub(username, password="pw1", **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    base = dict(id=None, username=username, password=password, tenant_id=1,
                status="enabled")
    base.update(kw)
    return subscribers_repo.upsert_subscriber(Subscriber(**base))


def _auth(username, password="pw1", mac=""):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password=password,
                                 tenant_id=1, calling_station_id=mac))


# ─────────────── effective_limit precedence ───────────────

def test_device_count_overrides_plan_concurrent():
    """device_count (1) يَغلب plan.concurrent_sessions (5)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services import device_limit
        plan = AccessPlan(id=1, tenant_id=1, name="P", plan_type="time",
                          concurrent_sessions=5)
        sub = _mk_sub("u_dc", device_count=1, plan_id=None)
        limit, mac_aware = device_limit.effective_limit(sub, plan)
        assert limit == 1
        assert mac_aware is True


def test_override_concurrent_beats_device_count():
    app = _fresh_app()
    with app.app_context():
        from app.radius.services import device_limit
        sub = _mk_sub("u_ov", device_count=2, override_concurrent=7)
        limit, mac_aware = device_limit.effective_limit(sub, None)
        assert limit == 7
        assert mac_aware is False  # مسار override = عدّ خام (سقف صارم)


# ─────────────── reject mode ───────────────

def test_limit_reject_returns_access_reject_with_message():
    """جهاز ثانٍ (MAC مختلف) عند device_count=1 → رفض + رسالة عربية."""
    app = _fresh_app()
    with app.app_context():
        _mk_sub("u_rej", device_count=1)
        _add_session(1, "u_rej", mac="AA:AA:AA:AA:AA:AA", session_id="s1")
        d = _auth("u_rej", mac="BB:BB:BB:BB:BB:BB")
        assert d.ok is False
        assert d.reason == "concurrent_limit"
        assert "بلغت الحد الأقصى" in d.message
        assert d.reply_attrs.get("Reply-Message")


def test_reauth_same_device_mac_allowed():
    """إعادة اتصال نفس الجهاز (نفس MAC) لا تُحتسَب — يُسمَح."""
    app = _fresh_app()
    with app.app_context():
        _mk_sub("u_re", device_count=1)
        _add_session(1, "u_re", mac="AA:AA:AA:AA:AA:AA", session_id="s1")
        d = _auth("u_re", mac="AA:AA:AA:AA:AA:AA")
        assert d.ok is True


def test_stale_session_does_not_count():
    """جلسة زومبي (خارج نافذة الحياة) لا تَحجب جهازًا جديدًا."""
    app = _fresh_app()
    with app.app_context():
        _mk_sub("u_stale", device_count=1)
        # جلسة قديمة جدًّا (60 دقيقة > نافذة 15) من جهاز آخر
        _add_session(1, "u_stale", mac="AA:AA:AA:AA:AA:AA",
                     session_id="s_old", age_min=60)
        d = _auth("u_stale", mac="BB:BB:BB:BB:BB:BB")
        assert d.ok is True, f"stale session should not block: {d.reason}"


def test_two_devices_within_limit_allowed():
    """device_count=2 → جهاز ثانٍ مسموح، ثالث مرفوض."""
    app = _fresh_app()
    with app.app_context():
        _mk_sub("u_two", device_count=2)
        _add_session(1, "u_two", mac="AA:AA:AA:AA:AA:AA", session_id="s1")
        # جهاز ثانٍ ضمن الحدّ
        assert _auth("u_two", mac="BB:BB:BB:BB:BB:BB").ok is True
        _add_session(1, "u_two", mac="BB:BB:BB:BB:BB:BB", session_id="s2")
        # جهاز ثالث يتجاوز
        d3 = _auth("u_two", mac="CC:CC:CC:CC:CC:CC")
        assert d3.ok is False
        assert d3.reason == "concurrent_limit"


# ─────────────── replace mode ───────────────

def test_limit_replace_kicks_oldest_and_allows():
    """وضع replace → يُغلق أقدم جلسة جهازٍ آخر ثمّ يَسمح بالجديدة."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.db.connection import db
        _mk_sub("u_rep", device_count=1, device_limit_mode="replace")
        # أقدم جلسة (age 5) + نُريد إغلاقها عند الاستبدال
        _add_session(1, "u_rep", mac="AA:AA:AA:AA:AA:AA",
                     session_id="s_oldest", age_min=5)
        d = _auth("u_rep", mac="BB:BB:BB:BB:BB:BB")
        assert d.ok is True, f"replace should allow new login: {d.reason}"
        # الجلسة الأقدم أُغلقت (acctstoptime مكتوب)
        row = db().execute(
            "SELECT acctstoptime, acctterminatecause FROM radacct "
            "WHERE acctsessionid='s_oldest'").fetchone()
        assert row["acctstoptime"], "oldest session should be closed"
        assert row["acctterminatecause"] == "Device-Limit-Replace"


def test_global_mode_setting_drives_replace():
    """الافتراض العام (billing.device_limit_mode=replace) يُطبَّق حين لا تجاوز
    per-subscriber."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "billing.device_limit_mode", "replace")
        _mk_sub("u_glob", device_count=1, device_limit_mode="")  # يَرث العام
        _add_session(1, "u_glob", mac="AA:AA:AA:AA:AA:AA",
                     session_id="s_g", age_min=3)
        d = _auth("u_glob", mac="BB:BB:BB:BB:BB:BB")
        assert d.ok is True
        row = db().execute(
            "SELECT acctstoptime FROM radacct WHERE acctsessionid='s_g'").fetchone()
        assert row["acctstoptime"], "global replace should close oldest"


# ─────────────── force_close (canonical accounting-stop) ───────────────

def test_force_close_writes_acctstoptime():
    """session_reconciler.force_close يُغلق الصفّ المفتوح ويَكتب acctstoptime."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import session_reconciler
        _add_session(1, "u_fc", mac="AA:AA:AA:AA:AA:AA", session_id="s_fc")
        n = session_reconciler.force_close(1, "u_fc", session_id="s_fc")
        assert n == 1
        row = db().execute(
            "SELECT acctstoptime, acctterminatecause FROM radacct "
            "WHERE acctsessionid='s_fc'").fetchone()
        assert row["acctstoptime"]
        assert row["acctterminatecause"] == session_reconciler.CAUSE_FORCE
        # idempotent — لا يُغلق مرّة ثانية
        assert session_reconciler.force_close(1, "u_fc", session_id="s_fc") == 0


def test_force_close_scoped_to_tenant():
    """الإغلاق الإجباري لا يَلمس مستأجرًا آخر."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import session_reconciler
        _add_session(1, "shared", mac="AA:AA:AA:AA:AA:AA", session_id="s_t1")
        _add_session(2, "shared", mac="AA:AA:AA:AA:AA:AA", session_id="s_t2")
        session_reconciler.force_close(1, "shared")
        # المستأجر 2 لم يُمَسّ
        row = db().execute(
            "SELECT acctstoptime FROM radacct WHERE acctsessionid='s_t2'").fetchone()
        assert not row["acctstoptime"]


# ─────────────── الانحدار الإنتاجيّ: صيغة طابع FreeRADIUS (مسافة) ───────────────
# هذه الاختبارات تُعيد إنتاج البق الذي رآه المالك: جهازان في الإنتاج بحدّ
# device_count=1 لم يُحجَب الثاني. الجذر: FreeRADIUS يَكتب الطابع الزمنيّ
# ``YYYY-MM-DD HH:MM:SS`` (مسافة)، والمقارنة المعجمية مع عتبة ISO ``T...Z``
# تَجعل كلّ جلسة حيّة تبدو زومبي فيُرجع العدّ 0 ولا يُنفَّذ الحدّ أبدًا.

def test_reject_freeradius_timestamp_format():
    """جلسة جهازٍ أوّل بصيغة FreeRADIUS (مسافة، حيّة الآن) تَحجب الثاني.

    هذا هو سيناريو المالك بالضبط — قبل الإصلاح كان العدّ يُرجع 0 (المقارنة
    المعجمية تَعدّها زومبي) فيُسمَح للجهاز الثاني خطأً."""
    app = _fresh_app()
    with app.app_context():
        _mk_sub("u_fr", device_count=1)
        _add_session(1, "u_fr", mac="AA:AA:AA:AA:AA:AA", session_id="s_fr",
                     age_min=0, fmt="freeradius")  # حيّة الآن، صيغة الإنتاج
        d = _auth("u_fr", mac="BB:BB:BB:BB:BB:BB")
        assert d.ok is False, "second device must be rejected (freeradius ts)"
        assert d.reason == "concurrent_limit"
        assert "بلغت الحد الأقصى" in d.message


def test_replace_freeradius_timestamp_format():
    """وضع replace مع صيغة FreeRADIUS → يُغلق الأقدم ويَسمح بالجديد."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.db.connection import db
        _mk_sub("u_frr", device_count=1, device_limit_mode="replace")
        _add_session(1, "u_frr", mac="AA:AA:AA:AA:AA:AA", session_id="s_frr",
                     age_min=2, fmt="freeradius")
        d = _auth("u_frr", mac="BB:BB:BB:BB:BB:BB")
        assert d.ok is True, f"replace should allow: {d.reason}"
        row = db().execute(
            "SELECT acctstoptime, acctterminatecause FROM radacct "
            "WHERE acctsessionid='s_frr'").fetchone()
        assert row["acctstoptime"], "oldest (freeradius ts) should be closed"
        assert row["acctterminatecause"] == "Device-Limit-Replace"


def test_stale_freeradius_session_still_does_not_block():
    """جلسة زومبي بصيغة FreeRADIUS (قديمة جدًّا) ما زالت لا تَحجب — الإصلاح
    لا يُعيد البلوك الكاذب للجلسات الميّتة، يَحترم النافذة بالتحليل الصحيح."""
    app = _fresh_app()
    with app.app_context():
        _mk_sub("u_frstale", device_count=1)
        _add_session(1, "u_frstale", mac="AA:AA:AA:AA:AA:AA",
                     session_id="s_frstale", age_min=60, fmt="freeradius")
        d = _auth("u_frstale", mac="BB:BB:BB:BB:BB:BB")
        assert d.ok is True, f"stale freeradius session must not block: {d.reason}"


def test_missing_calling_station_id_still_counts_other_device():
    """عنوان MAC مفقود في الطلب لا يُلغي الحدّ: جلسة مفتوحة لجهازٍ آخر تُحتسَب
    فلا يَتسلّل جهازٌ ثانٍ بلا MAC. (بق الاستبعاد mac-aware حين MAC فارغ.)"""
    app = _fresh_app()
    with app.app_context():
        _mk_sub("u_nomac", device_count=1)
        # جلسة أولى بلا MAC أيضًا (FreeRADIUS قد لا يُرسل calling-station-id)
        _add_session(1, "u_nomac", mac="", session_id="s_nomac1",
                     age_min=0, fmt="freeradius")
        d = _auth("u_nomac", mac="")  # طلب ثانٍ بلا MAC
        assert d.ok is False, "missing MAC must not bypass the limit"
        assert d.reason == "concurrent_limit"


def test_parse_acct_dt_both_formats():
    """دالّة التحليل تَقبل صيغتَي الطابع الزمنيّ وتُسقط الفارغ/التالف."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.services import device_limit as dl
        assert dl._parse_acct_dt("2026-06-30 11:10:20") is not None      # freeradius
        assert dl._parse_acct_dt("2026-06-30T11:10:20.876586Z") is not None  # iso
        assert dl._parse_acct_dt("") is None
        assert dl._parse_acct_dt(None) is None
        assert dl._parse_acct_dt("not-a-date") is None
        # الصيغتان لنفس اللحظة تُحلَّلان لقيمتين متقاربتين (ثوانٍ).
        a = dl._parse_acct_dt("2026-06-30 11:10:20")
        b = dl._parse_acct_dt("2026-06-30T11:10:20Z")
        assert abs((a - b).total_seconds()) < 1
