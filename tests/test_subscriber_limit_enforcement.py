"""اختبارات إنفاذ حدود المشترك الفرديّة في policy_engine (Wave 1).

يُغطّي ثلاث عائلات حقول كانت مُخزَّنة بلا إنفاذ ثمّ صارت تُنفَّذ عند المصادقة،
مع قاعدة «تجاوز المشترك يَغلب الباقة حين يُضبَط صراحةً، وإلّا تَسقط للباقة»:

  1. كوتا فرديّة (combined/download/upload + quota_limit_enabled).
  2. حدود وقت الاتصال (إجماليّ + يوميّ محلّي من acctsessiontime).
  3. جدول الاتصال الخاصّ (connection_schedule/working_days) يَتجاوز جدول الباقة.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _fresh_app():
    tmp = tempfile.mkdtemp(prefix="hr_test_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


def _seed_radacct_session(tenant_id, username, *, session_time, start,
                          calling_station_id=""):
    """يُدرج جلسة radacct واحدة بـ acctsessiontime + acctstarttime (UTC isoformat
    +Z تمامًا كمسار المحاسبة الحيّ).

    ``calling_station_id`` = MAC الجهاز؛ جلسات النطاق العريض (PPPoE) والهوت سبوت
    الحقيقيّة تَحمله دائمًا، ويُستعمَل لاستبعاد «نفس الجهاز» عند إعادة المصادقة.
    """
    from app.radius.db.connection import transaction
    ts = start.isoformat() + "Z"
    with transaction() as conn:
        conn.execute(
            "INSERT INTO radacct(tenant_id, username, acctstarttime, "
            "acctupdatetime, acctsessiontime, callingstationid, "
            "acctinputoctets, acctoutputoctets) "
            "VALUES(?,?,?,?,?,?,0,0)",
            (tenant_id, username, ts, ts, int(session_time),
             calling_station_id),
        )


# ─────────────────────── 1. كوتا فرديّة ───────────────────────


def test_subscriber_quota_below_plan_denies_at_subscriber_quota():
    """تجاوز المشترك (50MB) أقلّ من الباقة (1000MB) + استهلاك 60MB → يُرفض عند
    كوتا المشترك لا الباقة."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Big", plan_type="data",
            quota_total_mb=1000, enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="u1", password="p", status="enabled",
            plan_id=plan.id, quota_limit_enabled=True, combined_quota_mb=50,
            used_bytes_in=60 * 1048576, used_bytes_out=0))
        d = authorize(AuthRequest(username="u1", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "quota_exhausted", d.reason


def test_subscriber_quota_disabled_falls_back_to_plan():
    """quota_limit_enabled=False ولا كوتا فرديّة → تُطبَّق كوتا الباقة فقط.
    استهلاك (60MB) دون الباقة (1000MB) → يُسمَح (لو طُبّقت كوتا المشترك خطأً
    لانهار)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Big", plan_type="data",
            quota_total_mb=1000, enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="u2", password="p", status="enabled",
            plan_id=plan.id, quota_limit_enabled=False, combined_quota_mb=0,
            used_bytes_in=60 * 1048576, used_bytes_out=0))
        d = authorize(AuthRequest(username="u2", password="p", tenant_id=1))
        assert d.ok is True, f"{d.reason}: {d.message}"


def test_no_quota_set_plan_behavior_unchanged():
    """لا كوتا مشترك ولا باقة → لا فحص كوتا (سلوك غير متغيّر)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Flat", plan_type="time", enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="u3", password="p", status="enabled",
            plan_id=plan.id, used_bytes_in=9999 * 1048576))
        d = authorize(AuthRequest(username="u3", password="p", tenant_id=1))
        assert d.ok is True, d.reason


# ─────────────────────── 2. حدود وقت الاتصال ───────────────────────


def test_total_time_cap_blocks_lifetime():
    """سقف إجماليّ 30 دقيقة + استهلاك مُحاسَب 31 دقيقة → يُرفض."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="t1", password="p", status="enabled",
            connection_time_limit_enabled=True, total_connection_time_min=30))
        _seed_radacct_session(1, "t1", session_time=31 * 60,
                              start=datetime.utcnow() - timedelta(days=2))
        d = authorize(AuthRequest(username="t1", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "time_total_exhausted", d.reason


def test_daily_time_cap_blocks_then_resets_next_local_day():
    """سقف يوميّ 10 دقائق: جلسة اليوم 11 دقيقة → يُرفض؛ ولو كان الاستهلاك في
    يومٍ سابق فقط → يُسمَح (العدّاد اليوميّ يُعاد ضبطه لكلّ يومٍ محلّي)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        # (أ) استهلاك اليوم يتجاوز السقف → رفض.
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="d1", password="p", status="enabled",
            connection_time_limit_enabled=True, daily_connection_time_min=10))
        _seed_radacct_session(1, "d1", session_time=11 * 60,
                              start=datetime.utcnow())
        d = authorize(AuthRequest(username="d1", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "time_daily_exhausted", d.reason

        # (ب) مشترك آخر بنفس السقف لكن استهلاكه قبل خمسة أيام فقط → اليوم 0 →
        # يُسمَح (إعادة ضبط يوميّة).
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="d2", password="p", status="enabled",
            connection_time_limit_enabled=True, daily_connection_time_min=10))
        _seed_radacct_session(1, "d2", session_time=99 * 60,
                              start=datetime.utcnow() - timedelta(days=5))
        d2 = authorize(AuthRequest(username="d2", password="p", tenant_id=1))
        assert d2.ok is True, f"{d2.reason}: {d2.message}"


def test_time_cap_emits_session_timeout():
    """سقف يوميّ 10 دقائق وبلا استهلاك اليوم → قبول مع Session-Timeout ≤ 600s
    كي يُنفّذ الـNAS الحدّ."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="t2", password="p", status="enabled",
            connection_time_limit_enabled=True, daily_connection_time_min=10))
        d = authorize(AuthRequest(username="t2", password="p", tenant_id=1))
        assert d.ok is True, d.reason
        assert "Session-Timeout" in d.reply_attrs
        assert 0 < int(d.reply_attrs["Session-Timeout"]) <= 600


def test_no_time_cap_set_plan_behavior_unchanged():
    """لا سقف وقت مشترك ولا باقة → لا فحص وقت → سماح.

    إعادة المصادقة من نفس الجهاز (نفس MAC الذي تَحمله جلسات النطاق العريض/
    الهوت سبوت الحقيقيّة دائمًا) → جلسته القائمة تُستبعَد فلا يُرفَض بحدّ الجهاز.
    """
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="t3", password="p", status="enabled"))
        mac = "AA:BB:CC:DD:EE:01"
        _seed_radacct_session(1, "t3", session_time=99999,
                              start=datetime.utcnow(), calling_station_id=mac)
        d = authorize(AuthRequest(username="t3", password="p", tenant_id=1,
                                  calling_station_id=mac))
        assert d.ok is True, d.reason


def test_reauth_same_device_excluded_but_different_device_rejected():
    """نفس MAC = إعادة اتصال لنفس الجهاز → يُستبعَد → سماح؛ MAC مختلف = جهاز ثانٍ
    حقيقيّ يَتجاوز حدّ الجهاز الواحد (device_count الافتراضيّ = 1) → رفض.
    لا تغيير في منطق المصادقة — فقط محاكاة الـ MAC الذي تُرسله الجلسات الحقيقيّة."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="t4", password="p", status="enabled"))
        mac = "11:22:33:44:55:01"
        _seed_radacct_session(1, "t4", session_time=10,
                              start=datetime.utcnow(), calling_station_id=mac)
        # نفس الجهاز → سماح
        same = authorize(AuthRequest(username="t4", password="p", tenant_id=1,
                                     calling_station_id=mac))
        assert same.ok is True, f"same device should pass: {same.reason}"
        # جهاز مختلف (MAC مختلف) → يُرفَض عند حدّ الجهاز الواحد
        other = authorize(AuthRequest(username="t4", password="p", tenant_id=1,
                                      calling_station_id="11:22:33:44:55:02"))
        assert other.ok is False and other.reason == "concurrent_limit", \
            f"different device should be rejected, got {other.reason}"


# ─────────────────────── 3. جدول الاتصال ───────────────────────


def _schedule_today_full(local_weekday):
    return json.dumps({"windows": [
        {"days": [_DAYS[local_weekday]], "from": "00:00", "to": ""}]})


def _schedule_other_day_only(local_weekday):
    other = _DAYS[(local_weekday + 1) % 7]
    return json.dumps({"windows": [{"days": [other], "from": "", "to": ""}]})


def _local_weekday(tenant_id=1):
    from app.radius.core import system_config
    return system_config.local_now(tenant_id).weekday()


def test_subscriber_schedule_outside_denied():
    """جدول المشترك يَسمح بيومٍ غير اليوم فقط → خارج النافذة → رفض برسالة."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        wd = _local_weekday()
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="s1", password="p", status="enabled",
            connection_schedule=_schedule_other_day_only(wd)))
        d = authorize(AuthRequest(username="s1", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "outside_schedule", d.reason
        assert d.reply_attrs.get("Reply-Message")


def test_subscriber_schedule_inside_allowed():
    """جدول المشترك يَشمل اليوم كاملًا → ضمن النافذة → سماح."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        wd = _local_weekday()
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="s2", password="p", status="enabled",
            connection_schedule=_schedule_today_full(wd)))
        d = authorize(AuthRequest(username="s2", password="p", tenant_id=1))
        assert d.ok is True, f"{d.reason}: {d.message}"


def test_subscriber_schedule_overrides_plan_allow():
    """الباقة تَحجب (allowed_days = يومٌ غير اليوم) لكن جدول المشترك يَسمح كامل
    اليوم → السماح (تجاوز المشترك يَغلب جدول الباقة)."""
    app = _fresh_app()
    with app.app_context():
        from datetime import datetime as _dt
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        utc_wd = _dt.utcnow().weekday()
        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Restricted", plan_type="time",
            allowed_days=(_DAYS[(utc_wd + 3) % 7],), enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="s3", password="p", status="enabled",
            plan_id=plan.id,
            connection_schedule=_schedule_today_full(_local_weekday())))
        d = authorize(AuthRequest(username="s3", password="p", tenant_id=1))
        assert d.ok is True, f"{d.reason}: {d.message}"


def test_subscriber_schedule_overrides_plan_block():
    """الباقة تَسمح (كلّ الأيام) لكن جدول المشترك يَحجب اليوم → رفض (تجاوز
    المشترك يَغلب حتى للحجب)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Open", plan_type="time",
            allowed_days=_DAYS, enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="s4", password="p", status="enabled",
            plan_id=plan.id,
            connection_schedule=_schedule_other_day_only(_local_weekday())))
        d = authorize(AuthRequest(username="s4", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "outside_schedule", d.reason


def test_no_schedule_set_plan_schedule_applies():
    """لا جدول مشترك → جدول الباقة فقط هو الحاكم (سلوك غير متغيّر): الباقة
    تَحجب اليوم → رفض outside_days."""
    app = _fresh_app()
    with app.app_context():
        from datetime import datetime as _dt
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        utc_wd = _dt.utcnow().weekday()
        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="PlanOnly", plan_type="time",
            allowed_days=(_DAYS[(utc_wd + 2) % 7],), enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="s5", password="p", status="enabled",
            plan_id=plan.id))
        d = authorize(AuthRequest(username="s5", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "outside_days", d.reason


def test_working_days_csv_fallback_enforced():
    """احتياط legacy: working_days CSV بيومٍ غير اليوم → رفض outside_days."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        other = _DAYS[(_local_weekday() + 2) % 7]
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="s6", password="p", status="enabled",
            working_days=other))
        d = authorize(AuthRequest(username="s6", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "outside_days", d.reason
