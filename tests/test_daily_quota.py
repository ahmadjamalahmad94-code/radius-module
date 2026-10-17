"""الكوتة اليوميّة — سقفٌ يتجدّد منتصفَ الليل وقرارٌ بعد النفاد.

تُغطّي الطبقاتِ التي يَسهل أن تُبنى «منظرًا»:
  • العدّادُ يُجمَّع بالفرق لا بالقيمة (interim لا يحتسب الجلسةَ مرّتين).
  • تغيّرُ ختمِ اليوم المحلّيّ يصفّر العدّادَ من نفسه — بلا مهمّةٍ مجدولة.
  • «قطع» يَرفض المصادقة، و«تخفيف/سرعة مخصّصة» يَسمحان ويُغيّران السرعةَ
    في ردّ القبول فعلًا.
  • اليومُ محلّيٌّ لا UTC.

شغّل هذا الملفّ وحدَه (عزلُ الاختبارات لكلّ ملفّ).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

MB = 1_048_576


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_dq_")
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


def _sub(**kw):
    from app.radius.core.types import Subscriber
    base = dict(id=1, username="bob", password="p", tenant_id=1,
                status="enabled", user_type="subscriber")
    base.update(kw)
    return Subscriber(**base)


def _plan(**kw):
    from app.radius.core.types import AccessPlan
    base = dict(id=1, tenant_id=1, name="P", speed_down_kbps=8000,
                speed_up_kbps=4000)
    base.update(kw)
    return AccessPlan(**base)


def _seed_subscriber(username="bob", tenant_id=1):
    from app.radius.db.connection import db
    from app.radius.db.repos import tenants_repo
    tenants_repo.ensure_default_tenant()
    db().execute(
        "INSERT OR IGNORE INTO subscribers(tenant_id,username,password,status,"
        "user_type,created_at) VALUES(?,?,'p','enabled','subscriber',"
        "datetime('now'))", (tenant_id, username))
    db().commit()


# ───────────────────────── السقفُ الفعّال ─────────────────────────

def test_subscriber_cap_overrides_plan(app):
    with app.app_context():
        from app.radius.services import daily_quota as dq
        sub = _sub(daily_quota_mb=200)
        plan = _plan(daily_combined_quota_mb=500)
        assert dq.effective_cap_mb(sub, plan) == 200


def test_plan_cap_used_when_subscriber_has_none(app):
    with app.app_context():
        from app.radius.services import daily_quota as dq
        assert dq.effective_cap_mb(_sub(), _plan(daily_combined_quota_mb=500)) == 500


def test_separate_down_up_caps_sum(app):
    with app.app_context():
        from app.radius.services import daily_quota as dq
        plan = _plan(daily_download_quota_mb=300, daily_upload_quota_mb=100)
        assert dq.effective_cap_mb(_sub(), plan) == 400


def test_no_cap_never_exhausts(app):
    with app.app_context():
        from app.radius.services import daily_quota as dq
        sub = _sub(daily_used_bytes=999 * MB, daily_usage_day=dq.local_day(1))
        assert dq.is_exhausted(sub, _plan()) is False


# ───────────────────── العدّادُ والتجديدُ الذاتيّ ─────────────────────

def test_usage_accumulates_within_the_same_day(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import daily_quota as dq
        _seed_subscriber()
        dq.add_usage(1, "bob", 10 * MB)
        dq.add_usage(1, "bob", 5 * MB)
        sub = subscribers_repo.get_subscriber(1, "bob")
        assert dq.used_bytes_today(sub) == 15 * MB


def test_stamp_of_another_day_reads_as_zero(app):
    """🔴 جوهرُ التجديد: ختمٌ قديم ⇒ صفر بلا أيّ مهمّةٍ مجدولة."""
    with app.app_context():
        from app.radius.services import daily_quota as dq
        stale = _sub(daily_used_bytes=900 * MB, daily_usage_day="2000-01-01")
        assert dq.used_bytes_today(stale) == 0
        assert dq.is_exhausted(stale, _plan(daily_combined_quota_mb=100)) is False


def test_add_usage_resets_counter_when_day_rolls(app):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import daily_quota as dq
        _seed_subscriber()
        db().execute("UPDATE subscribers SET daily_used_bytes=?, daily_usage_day=? "
                     "WHERE tenant_id=1 AND username='bob'",
                     (900 * MB, "2000-01-01"))
        db().commit()
        dq.add_usage(1, "bob", 7 * MB)
        sub = subscribers_repo.get_subscriber(1, "bob")
        assert sub.daily_usage_day == dq.local_day(1)
        assert sub.daily_used_bytes == 7 * MB      # لا 907


def test_day_stamp_is_tenant_local_not_utc(app):
    """التوقيتُ المحلّيّ (Asia/Gaza) قد يَسبق UTC بيومٍ قرب منتصف الليل."""
    with app.app_context():
        from app.radius.core import system_config
        from app.radius.services import daily_quota as dq
        assert dq.local_day(1) == system_config.local_today(1).isoformat()


# ─────────────────── المحاسبة تُغذّي بالفرق لا بالقيمة ───────────────────

def test_interim_feeds_delta_not_total(app):
    """جلسةٌ تُبلّغ 10 ثمّ 25 ميجا ⇒ عدّادُ اليوم 25 لا 35."""
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.accounting_events import AccountingEventsService
        from app.radius.services import daily_quota as dq
        _seed_subscriber()
        eng = AccountingEventsService()
        common = dict(username="bob", acct_session_id="s1",
                      nas_ip_address="10.0.0.1", framed_ip_address="10.1.1.5")
        eng.ingest(tenant_id=1, payload=dict(
            status_type="Start", session_time=0, input_octets=0,
            output_octets=0, **common))
        eng.ingest(tenant_id=1, payload=dict(
            status_type="Interim-Update", session_time=60,
            input_octets=6 * MB, output_octets=4 * MB, **common))
        eng.ingest(tenant_id=1, payload=dict(
            status_type="Interim-Update", session_time=120,
            input_octets=15 * MB, output_octets=10 * MB, **common))
        sub = subscribers_repo.get_subscriber(1, "bob")
        assert dq.used_bytes_today(sub) == 25 * MB


# ─────────────────────────── القرارُ بعد النفاد ───────────────────────────

def _exhausted_sub(**kw):
    from app.radius.services import daily_quota as dq
    kw.setdefault("daily_quota_mb", 100)
    kw["daily_used_bytes"] = 150 * MB
    kw["daily_usage_day"] = dq.local_day(1)
    return _sub(**kw)


def test_disconnect_action_rejects_auth(app):
    with app.app_context():
        from app.radius.services.policy_engine import _check_daily_quota
        sub = _exhausted_sub(daily_quota_action="disconnect")
        decision = _check_daily_quota(sub, _plan())
        assert decision is not None and decision.ok is False
        assert decision.reason == "daily_quota_exhausted"


def test_reduce_speed_action_allows_auth(app):
    with app.app_context():
        from app.radius.services.policy_engine import _check_daily_quota
        sub = _exhausted_sub(daily_quota_action="reduce_speed")
        assert _check_daily_quota(sub, _plan()) is None


def test_custom_speed_is_written_in_mbps_and_applied(app):
    """«سرعة مخصّصة 2 ميجا» ⇒ 2000 كيلوبت في ردّ القبول."""
    with app.app_context():
        from app.radius.services import daily_quota as dq
        sub = _exhausted_sub(daily_quota_action="custom_speed",
                             daily_quota_speed_down_kbps=2000,
                             daily_quota_speed_up_kbps=1000)
        assert dq.throttle_kbps(sub, _plan()) == (2000, 1000)
        rate = dq.throttle_rate_limit(sub, _plan())
        assert "2000k" in rate or "2000K" in rate


def test_custom_speed_without_upload_mirrors_download(app):
    with app.app_context():
        from app.radius.services import daily_quota as dq
        sub = _exhausted_sub(daily_quota_action="custom_speed",
                             daily_quota_speed_down_kbps=1500)
        assert dq.throttle_kbps(sub, _plan()) == (1500, 1500)


def test_action_falls_back_to_plan_then_disconnect(app):
    with app.app_context():
        from app.radius.services import daily_quota as dq
        assert dq.effective_action(_sub(), _plan(daily_quota_action="reduce_speed")) \
            == "reduce_speed"
        assert dq.effective_action(_sub(), _plan()) == "disconnect"


# ─────────────── ردُّ القبول يَحمل السرعةَ المخفَّضة فعلًا ───────────────

def test_accept_reply_carries_throttled_rate_and_midnight_timeout(app):
    """ليست «منظرًا»: الردُّ نفسُه يَخرج بسرعةٍ مخفَّضة ومهلةٍ تنتهي منتصفَ الليل."""
    with app.app_context():
        from app.radius.services import policy_engine as pe
        sub = _exhausted_sub(daily_quota_action="custom_speed",
                             daily_quota_speed_down_kbps=2000,
                             daily_quota_speed_up_kbps=1000,
                             download_speed_kbps=8000, upload_speed_kbps=4000)
        out = pe._build_accept_attrs(sub, _plan())
        assert "2000k" in out.get("Mikrotik-Rate-Limit", "")
        assert int(out.get("Session-Timeout") or 0) > 0
        assert int(out["Session-Timeout"]) <= 86400


def test_accept_reply_untouched_when_not_exhausted(app):
    with app.app_context():
        from app.radius.services import policy_engine as pe
        sub = _sub(daily_quota_mb=100, daily_used_bytes=1 * MB,
                   download_speed_kbps=8000, upload_speed_kbps=4000)
        out = pe._build_accept_attrs(sub, _plan())
        assert "2000k" not in out.get("Mikrotik-Rate-Limit", "")


# ─────────────────────────── التصفيرُ اليدويّ ───────────────────────────

def test_manual_reset_clears_counter_and_state(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import daily_quota as dq
        _seed_subscriber()
        dq.add_usage(1, "bob", 50 * MB)
        dq.set_state(1, "bob", "throttled")
        dq.reset(1, "bob")
        sub = subscribers_repo.get_subscriber(1, "bob")
        assert sub.daily_used_bytes == 0
        assert sub.daily_quota_state == ""


# ──────────── نموذجا الباقة والمشترك يَحفظان الحقولَ فعلًا ────────────

def test_plan_form_parses_daily_quota_fields(app):
    """نفسُ دالّةِ المسار ونفسُ request.form — لا تحليلَ مُختلَق."""
    with app.test_request_context("/admin/radius/plans", method="POST", data={
            "name": "P-DAILY", "plan_type": "quota", "service_type": "pppoe",
            "speed_down_kbps": "8000", "speed_up_kbps": "4000",
            "daily_combined_quota_mb": "10",
            "daily_quota_action": "custom_speed",
            "daily_quota_speed_down_mbps": "2",
            "daily_quota_speed_up_mbps": "1.5"}):
        from app.radius.routes import plans as plans_routes
        dto = plans_routes._form_to_dto()
    assert dto.daily_combined_quota_mb == 10
    assert dto.daily_quota_action == "custom_speed"
    assert dto.daily_quota_speed_down_kbps == 2000     # 2 ميجا
    assert dto.daily_quota_speed_up_kbps == 1500       # 1.5 ميجا


def test_plan_form_rejects_unknown_action(app):
    with app.test_request_context("/admin/radius/plans", method="POST", data={
            "name": "P", "plan_type": "quota", "service_type": "pppoe",
            "speed_down_kbps": "8000", "speed_up_kbps": "4000",
            "daily_quota_action": "format_c_drive"}):
        from app.radius.routes import plans as plans_routes
        dto = plans_routes._form_to_dto()
    assert dto.daily_quota_action == "disconnect"


def test_subscriber_form_parses_daily_override(app):
    with app.test_request_context("/admin/radius/users", method="POST", data={
            "username": "u1", "password": "p", "status": "enabled",
            "daily_quota_mb": "500",
            "daily_quota_action": "reduce_speed",
            "daily_quota_speed_down_mbps": "1",
            "daily_quota_speed_up_mbps": "0.5"}):
        from app.radius.routes import users as users_routes
        dto = users_routes._form_dto()
    assert dto.daily_quota_mb == 500
    assert dto.daily_quota_action == "reduce_speed"
    assert dto.daily_quota_speed_down_kbps == 1000
    assert dto.daily_quota_speed_up_kbps == 500


def test_subscriber_form_empty_action_follows_plan(app):
    with app.test_request_context("/admin/radius/users", method="POST", data={
            "username": "u1", "password": "p", "status": "enabled"}):
        from app.radius.routes import users as users_routes
        dto = users_routes._form_dto()
    assert dto.daily_quota_action == ""
    assert dto.daily_quota_mb == 0
