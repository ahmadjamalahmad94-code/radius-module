"""«ماذا بعد نفاد الكوتة اليوميّة؟» — قطعٌ أم تخفيفٌ أم سرعةٌ مخصّصة.

الكوتةُ اليوميّة نفسُها مملوكةٌ لـ``quota_period`` (نوافذُ محلّيّة بخطّ
أساس تتجدّد منتصفَ الليل) وهي مُختبَرةٌ هناك. هذا الملفّ يُغطّي **القرار**
الجديد وحدَه، وأهمَّ ما فيه أنّه لا يُغيّر السلوكَ التاريخيّ لمن لم يَضبط
شيئًا: القطعُ يبقى الافتراض.

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
    tmp = tempfile.mkdtemp(prefix="hr_dqa_")
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


# ─────────────────────────── القرارُ الساري ───────────────────────────

def test_default_is_disconnect_unchanged_behaviour(app):
    """من لم يَضبط شيئًا يبقى على السلوك التاريخيّ: قطع."""
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        assert dqa.effective_action(_sub(), _plan()) == "disconnect"
        assert dqa.allows_connection(_sub(), _plan()) is False
        assert dqa.throttle_kbps(_sub(), _plan()) is None
        assert dqa.throttle_rate_limit(_sub(), _plan()) == ""


def test_plan_action_applies(app):
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        plan = _plan(daily_quota_action="reduce_speed")
        assert dqa.effective_action(_sub(), plan) == "reduce_speed"
        assert dqa.allows_connection(_sub(), plan) is True


def test_subscriber_overrides_plan(app):
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        sub = _sub(daily_quota_action="disconnect")
        plan = _plan(daily_quota_action="custom_speed",
                     daily_quota_speed_down_kbps=2000)
        assert dqa.effective_action(sub, plan) == "disconnect"
        assert dqa.allows_connection(sub, plan) is False


def test_unknown_action_falls_back_to_disconnect(app):
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        assert dqa.effective_action(_sub(daily_quota_action="nonsense"),
                                    _plan()) == "disconnect"


# ─────────────────────── السرعةُ المخصّصة بالميجابت ───────────────────────

def test_custom_speed_pair(app):
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        plan = _plan(daily_quota_action="custom_speed",
                     daily_quota_speed_down_kbps=2000,
                     daily_quota_speed_up_kbps=1000)
        assert dqa.throttle_kbps(_sub(), plan) == (2000, 1000)
        # «رفع/نزول» كباقي ردود القبول — لا «نزول صعود» بمسافة.
        assert dqa.throttle_rate_limit(_sub(), plan) == "1000k/2000k"


def test_custom_speed_without_upload_mirrors_download(app):
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        plan = _plan(daily_quota_action="custom_speed",
                     daily_quota_speed_down_kbps=1500)
        assert dqa.throttle_kbps(_sub(), plan) == (1500, 1500)


def test_custom_speed_with_no_number_falls_back_to_general_reduce(app):
    """«سرعة مخصّصة» بلا رقم لا تَفتح الخطّ ولا تَقطع — تُخفَّف عامًّا."""
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        plan = _plan(daily_quota_action="custom_speed")
        pair = dqa.throttle_kbps(_sub(), plan)
        assert pair is not None and pair[0] > 0


def test_subscriber_custom_speed_wins(app):
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        sub = _sub(daily_quota_action="custom_speed",
                   daily_quota_speed_down_kbps=3000,
                   daily_quota_speed_up_kbps=1500)
        plan = _plan(daily_quota_action="custom_speed",
                     daily_quota_speed_down_kbps=1000)
        assert dqa.throttle_kbps(sub, plan) == (3000, 1500)


def test_midnight_countdown_is_bounded(app):
    with app.app_context():
        from app.radius.services import daily_quota_action as dqa
        left = dqa.seconds_until_local_midnight(1)
        assert 60 <= left <= 86400


# ─────────────── المحرّك: رفضٌ أو سماحٌ حسب القرار ───────────────

def _exhausted_daily(monkeypatch_target, value="daily"):
    """يُجبر فحصَ النافذة على القول إنّ الكوتة اليوميّة نفدت."""
    monkeypatch_target.window_exhaustion = lambda *a, **k: value


def test_auth_rejected_when_action_is_disconnect(app, monkeypatch):
    with app.app_context():
        from app.radius.services import policy_engine as pe
        from app.radius.services import quota_period
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "daily")
        decision = pe._check_quota(_sub(), _plan(daily_combined_quota_mb=10))
        assert decision is not None and decision.ok is False


def test_auth_allowed_when_action_is_a_speed(app, monkeypatch):
    with app.app_context():
        from app.radius.services import policy_engine as pe
        from app.radius.services import quota_period
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "daily")
        plan = _plan(daily_combined_quota_mb=10,
                     daily_quota_action="custom_speed",
                     daily_quota_speed_down_kbps=2000)
        assert pe._check_quota(_sub(), plan) is None


def test_monthly_exhaustion_still_rejects_even_with_daily_action(app, monkeypatch):
    """القرارُ يخصّ النافذةَ اليوميّة وحدَها — الشهريّةُ تُرفض كما كانت."""
    with app.app_context():
        from app.radius.services import policy_engine as pe
        from app.radius.services import quota_period
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "monthly")
        plan = _plan(daily_quota_action="custom_speed",
                     daily_quota_speed_down_kbps=2000)
        decision = pe._check_quota(_sub(), plan)
        assert decision is not None and decision.ok is False


# ─────────────── ردُّ القبول يَحمل السرعةَ فعلًا ───────────────

def test_accept_reply_carries_custom_rate_and_midnight_timeout(app, monkeypatch):
    with app.app_context():
        from app.radius.services import policy_engine as pe
        from app.radius.services import quota_period
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "daily")
        plan = _plan(daily_quota_action="custom_speed",
                     daily_quota_speed_down_kbps=2000,
                     daily_quota_speed_up_kbps=1000)
        out = pe._build_accept_attrs(_sub(download_speed_kbps=8000,
                                          upload_speed_kbps=4000), plan)
        assert "2000k" in out.get("Mikrotik-Rate-Limit", "")
        assert 0 < int(out.get("Session-Timeout") or 0) <= 86400


def test_accept_reply_untouched_when_quota_not_exhausted(app, monkeypatch):
    with app.app_context():
        from app.radius.services import policy_engine as pe
        from app.radius.services import quota_period
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "")
        plan = _plan(daily_quota_action="custom_speed",
                     daily_quota_speed_down_kbps=2000)
        out = pe._build_accept_attrs(_sub(download_speed_kbps=8000,
                                          upload_speed_kbps=4000), plan)
        assert "2000k" not in out.get("Mikrotik-Rate-Limit", "")


def test_accept_reply_untouched_when_action_is_disconnect(app, monkeypatch):
    with app.app_context():
        from app.radius.services import policy_engine as pe
        from app.radius.services import quota_period
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "daily")
        out = pe._build_accept_attrs(_sub(download_speed_kbps=8000,
                                          upload_speed_kbps=4000), _plan())
        assert "512k" not in out.get("Mikrotik-Rate-Limit", "")


# ─────────────────────────── النموذجان ───────────────────────────

def test_plan_form_parses_action_and_mbps(app):
    with app.test_request_context("/admin/radius/plans", method="POST", data={
            "name": "P-DAILY", "plan_type": "quota", "service_type": "pppoe",
            "speed_down_kbps": "8000", "speed_up_kbps": "4000",
            "daily_combined_quota_mb": "10",
            "daily_quota_action": "custom_speed",
            "daily_quota_speed_down_mbps": "2",
            "daily_quota_speed_up_mbps": "1.5"}):
        from app.radius.routes import plans as plans_routes
        dto = plans_routes._form_to_dto()
    assert dto.daily_quota_action == "custom_speed"
    assert dto.daily_quota_speed_down_kbps == 2000
    assert dto.daily_quota_speed_up_kbps == 1500


def test_plan_form_rejects_unknown_action(app):
    with app.test_request_context("/admin/radius/plans", method="POST", data={
            "name": "P", "plan_type": "quota", "service_type": "pppoe",
            "speed_down_kbps": "8000", "speed_up_kbps": "4000",
            "daily_quota_action": "format_c_drive"}):
        from app.radius.routes import plans as plans_routes
        dto = plans_routes._form_to_dto()
    assert dto.daily_quota_action == "disconnect"


def test_subscriber_form_parses_override(app):
    with app.test_request_context("/admin/radius/users", method="POST", data={
            "username": "u1", "password": "p", "status": "enabled",
            "daily_quota_action": "reduce_speed",
            "daily_quota_speed_down_mbps": "1",
            "daily_quota_speed_up_mbps": "0.5"}):
        from app.radius.routes import users as users_routes
        dto = users_routes._form_dto()
    assert dto.daily_quota_action == "reduce_speed"
    assert dto.daily_quota_speed_down_kbps == 1000
    assert dto.daily_quota_speed_up_kbps == 500


def test_subscriber_form_empty_action_follows_plan(app):
    with app.test_request_context("/admin/radius/users", method="POST", data={
            "username": "u1", "password": "p", "status": "enabled"}):
        from app.radius.routes import users as users_routes
        dto = users_routes._form_dto()
    assert dto.daily_quota_action == ""


# ─────────────────────── العاملُ الدوريّ ───────────────────────

def _seed_online(username="bob", tenant_id=1, plan_id=None):
    from app.radius.db.connection import db
    from app.radius.db.repos import tenants_repo
    tenants_repo.ensure_default_tenant()
    db().execute(
        "INSERT OR IGNORE INTO subscribers(tenant_id,username,password,status,"
        "user_type,plan_id,created_at) VALUES(?,?,'p','enabled','subscriber',?,"
        "datetime('now'))", (tenant_id, username, plan_id))
    db().execute(
        "INSERT INTO radacct(tenant_id,username,acctsessionid,acctstarttime,"
        " acctsessiontime,acctinputoctets,acctoutputoctets)"
        " VALUES(?,?,?,datetime('now'),60,0,0)",
        (tenant_id, username, "w-%s" % username))
    db().commit()


def test_worker_throttles_once_then_stops(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import quota_period
        from app.workers import daily_quota_action_worker as w
        _seed_online()
        db().execute("UPDATE subscribers SET daily_quota_action='custom_speed',"
                     " daily_quota_speed_down_kbps=2000,"
                     " daily_quota_speed_up_kbps=1000 "
                     " WHERE tenant_id=1 AND username='bob'")
        db().commit()
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "daily")
        calls: list = []
        monkeypatch.setattr(w, "_coa_rate",
                            lambda t, u, r: calls.append((t, u, r)))
        stats = w.enforce_once()
        assert stats["throttled"] == 1
        assert calls and "2000k" in calls[0][2]
        assert subscribers_repo.get_subscriber(1, "bob").daily_quota_state \
            == "throttled"
        calls.clear()
        w.enforce_once()
        assert calls == []          # لا تكرار


def test_worker_restores_after_renewal(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import quota_period
        from app.workers import daily_quota_action_worker as w
        _seed_online()
        db().execute("UPDATE subscribers SET daily_quota_action='custom_speed',"
                     " daily_quota_speed_down_kbps=2000,"
                     " daily_quota_state='throttled' "
                     " WHERE tenant_id=1 AND username='bob'")
        db().commit()
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "")          # تجدَّدت
        monkeypatch.setattr(w, "_normal_rate", lambda t, u: "4M/8M")
        calls: list = []
        monkeypatch.setattr(w, "_coa_rate",
                            lambda t, u, r: calls.append((t, u, r)))
        stats = w.enforce_once()
        assert stats["restored"] == 1
        assert calls and calls[0][2] == "4M/8M"
        assert subscribers_repo.get_subscriber(1, "bob").daily_quota_state == ""


def test_worker_ignores_disconnect_accounts(app, monkeypatch):
    """قرارُ القطع شأنُ المحرّك — العاملُ لا يَلمسه ولا يُرسل CoA."""
    with app.app_context():
        from app.radius.services import quota_period
        from app.workers import daily_quota_action_worker as w
        _seed_online()
        monkeypatch.setattr(quota_period, "window_exhaustion",
                            lambda *a, **k: "daily")
        calls: list = []
        monkeypatch.setattr(w, "_coa_rate",
                            lambda t, u, r: calls.append((t, u, r)))
        stats = w.enforce_once()
        assert calls == [] and stats["throttled"] == 0


# ─────────────── العرض: القرارُ قالبٌ يُبصَم في كلّ كرت ───────────────

def _mk_plan(app):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo, tenants_repo
    tenants_repo.ensure_default_tenant()
    return plans_repo.upsert_plan(AccessPlan(
        id=None, tenant_id=1, name="OFFER-PLAN", plan_type="quota",
        speed_down_kbps=8000, speed_up_kbps=4000,
        daily_combined_quota_mb=10))


def test_offer_stores_action_and_custom_speed(app):
    with app.app_context():
        from app.radius.services.card_offers import CardOffersService
        plan = _mk_plan(app)
        svc = CardOffersService(tenant_id=1)
        offer = svc.create_offer(
            name="عرض يومي", duration_minutes=1440, wholesale=1, selling=2,
            plan_id=plan.id, daily_quota_action="custom_speed",
            daily_quota_speed_down_kbps=2000, daily_quota_speed_up_kbps=1000)
        assert offer["daily_quota_action"] == "custom_speed"
        assert offer["daily_quota_speed_down_kbps"] == 2000
        assert offer["daily_quota_speed_up_kbps"] == 1000


def test_offer_rejects_unknown_action(app):
    with app.app_context():
        from app.radius.services.card_offers import CardOffersService
        plan = _mk_plan(app)
        svc = CardOffersService(tenant_id=1)
        offer = svc.create_offer(name="x", duration_minutes=60, wholesale=1,
                                 selling=1, plan_id=plan.id,
                                 daily_quota_action="wipe_disk")
        assert offer["daily_quota_action"] == ""     # '' = اتبع الباقة


def test_offer_update_changes_action(app):
    with app.app_context():
        from app.radius.services.card_offers import CardOffersService
        plan = _mk_plan(app)
        svc = CardOffersService(tenant_id=1)
        offer = svc.create_offer(name="y", duration_minutes=60, wholesale=1,
                                 selling=1, plan_id=plan.id)
        assert offer["daily_quota_action"] == ""
        after = svc.update_offer(offer["id"], daily_quota_action="reduce_speed")
        assert after["daily_quota_action"] == "reduce_speed"
        kept = svc.update_offer(offer["id"], name="y2")
        assert kept["daily_quota_action"] == "reduce_speed"   # لا يُمسح بحفظٍ آخر


def test_generated_cards_inherit_the_offer_action(app):
    """كلّ كرتٍ يُولَّد يَحمل القرارَ على حسابه هو — فيَقرأه المحرّك مباشرةً."""
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.cards import get_cards_service
        plan = _mk_plan(app)
        batch, cards = get_cards_service().generate_batch(
            actor="tester", plan_id=plan.id, count=3,
            package_name="دفعة يوميّة",
            daily_quota_action="custom_speed",
            daily_quota_speed_down_kbps=2000,
            daily_quota_speed_up_kbps=1000)
        assert len(cards) == 3
        for card in cards:
            sub = subscribers_repo.get_subscriber(1, card.username)
            assert sub is not None, card.username
            assert sub.daily_quota_action == "custom_speed"
            assert sub.daily_quota_speed_down_kbps == 2000
            assert sub.daily_quota_speed_up_kbps == 1000


def test_generated_cards_without_offer_action_stay_empty(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.cards import get_cards_service
        plan = _mk_plan(app)
        _batch, cards = get_cards_service().generate_batch(
            actor="tester", plan_id=plan.id, count=2, package_name="عاديّة")
        for card in cards:
            sub = subscribers_repo.get_subscriber(1, card.username)
            assert sub.daily_quota_action == ""      # اتبع الباقة


# ───────── صفحة المشترك: الحقولُ موجودةٌ في الإنشاء كما في التعديل ─────────

def test_new_subscriber_context_carries_the_fields(app):
    """نموذجُ الإنشاء يَستعمل نفسَ القالب؛ فلا بدّ أن تَحمل سياقَه المفاتيحُ
    الجديدة وإلّا ظهر الحقلُ فارغًا أو انكسر القالب."""
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.routes import users as users_routes
        ctx = users_routes._sub_with_meta_for_template(
            Subscriber(id=None, username="", password="", status="enabled"))
    for key in ("daily_quota_action", "daily_quota_speed_down_kbps",
                "daily_quota_speed_up_kbps"):
        assert key in ctx, key
    assert ctx["daily_quota_action"] == ""          # الافتراض: حسب الباقة


def test_subscriber_form_template_has_the_three_controls():
    """حارسٌ على القالب نفسِه: الحقولُ الثلاثة مكتوبةٌ في صفحة المشترك."""
    import pathlib
    html = pathlib.Path("app/templates/radius/users_form.html").read_text(
        encoding="utf-8")
    assert 'name="daily_quota_action"' in html
    assert 'name="daily_quota_speed_down_mbps"' in html
    assert 'name="daily_quota_speed_up_mbps"' in html


def test_offers_template_has_the_controls_in_both_forms():
    import pathlib
    html = pathlib.Path("app/templates/radius/cards_offers.html").read_text(
        encoding="utf-8")
    assert html.count('name="daily_quota_action"') >= 2        # إنشاء + تعديل
    assert html.count('name="daily_quota_speed_down_mbps"') >= 2


def test_plan_form_template_has_the_controls():
    import pathlib
    html = pathlib.Path("app/templates/radius/plans_form.html").read_text(
        encoding="utf-8")
    assert 'name="daily_quota_action"' in html
    assert 'name="daily_quota_speed_down_mbps"' in html
