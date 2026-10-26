"""**مقدارُ** الكوتة اليوميّة على المشترك وعلى العرض (migration 203).

المالك (2026-10-26): «أضف خانة الكوتة اليومية (جيجا) على صفحة المشترك وعلى
عرض الكروت، والمشترك يغلب الباقة». كانت الكوتةُ اليوميّة على الباقة وحدَها؛
الآن يكتب المشغّل رقمًا بالجيجا على المشترك (أو على العرض فيُبصَم في كلّ
كرت)، ويُخزَّن ميجابايت، و0 = اتبع الباقة.

شغّل هذا الملفّ وحدَه (عزلُ الاختبارات لكلّ ملفّ).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_dqm_")
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


# ─────────────────────────── المشتركُ يَغلب الباقة ───────────────────────────

def test_no_own_quota_follows_the_plan(app):
    with app.app_context():
        from app.radius.services import quota_period as qp
        plan = _plan(daily_combined_quota_mb=2048, daily_download_quota_mb=1024)
        caps = qp.sub_window_caps(_sub(), plan)
    assert caps["daily"] == {"combined": 2048, "download": 1024, "upload": 0}


def test_own_quota_replaces_the_plan_daily_caps(app):
    """رقمُ المشترك يحلّ محلّ سقوف الباقة اليوميّة كلّها — حتّى سقفُ الاتجاه
    الأضيق لا يبقى، وإلّا قُطع قبل أن يبلغ ما كتبه المشغّل."""
    with app.app_context():
        from app.radius.services import quota_period as qp
        plan = _plan(daily_combined_quota_mb=2048, daily_download_quota_mb=1024,
                     monthly_combined_quota_mb=50_000)
        caps = qp.sub_window_caps(_sub(daily_quota_mb=5120), plan)
    assert caps["daily"] == {"combined": 5120, "download": 0, "upload": 0}
    assert caps["monthly"]["combined"] == 50_000          # الشهريّة للباقة


def test_own_quota_on_a_plan_without_daily_quota(app):
    with app.app_context():
        from app.radius.services import quota_period as qp
        caps = qp.sub_window_caps(_sub(daily_quota_mb=3072), _plan())
        assert qp.has_window_caps(caps)
        assert qp._has_any_quota(_sub(daily_quota_mb=3072), _plan())
        assert not qp._has_any_quota(_sub(), _plan())


def test_exhaustion_uses_the_subscriber_number(app, monkeypatch):
    """باقةٌ 1 جيجا ومشتركٌ 3 جيجا: استهلاكُ 2 جيجا لا يَنفد، و3 جيجا تَنفد."""
    with app.app_context():
        from app.radius.services import quota_period as qp
        monkeypatch.setattr(qp, "get_state", lambda *a, **k: {})
        plan = _plan(daily_combined_quota_mb=1024)
        sub = _sub(daily_quota_mb=3072)
        used = {"daily": (0, 2048 * qp.MIB), "monthly": (0, 0), "period": (0, 0)}
        monkeypatch.setattr(qp, "usage", lambda *a, **k: used)
        assert qp.window_exhaustion(sub, plan) == ""
        assert qp.window_exhaustion(_sub(), plan) == "daily"   # بلا تجاوز ⇒ الباقة
        used["daily"] = (0, 3072 * qp.MIB)
        assert qp.window_exhaustion(sub, plan) == "daily"


def test_quota_status_reports_the_subscriber_number(app, monkeypatch):
    with app.app_context():
        from app.radius.services import quota_period as qp
        monkeypatch.setattr(qp, "get_state", lambda *a, **k: {})
        monkeypatch.setattr(qp, "usage", lambda *a, **k: {
            "daily": (0, 0), "monthly": (0, 0), "period": (0, 0)})
        st = qp.quota_status(_sub(daily_quota_mb=5120), _plan())
    assert st["daily"]["combined"] == 5120
    assert st["has_quota"] is True


# ─────────────────────────── صفحةُ المشترك ───────────────────────────

def test_subscriber_form_parses_gigabytes(app):
    with app.test_request_context("/admin/radius/users", method="POST", data={
            "username": "u1", "password": "p", "status": "enabled",
            "daily_quota_gb": "2.5"}):
        from app.radius.routes import users as users_routes
        dto = users_routes._form_dto()
    assert dto.daily_quota_mb == 2560


def test_subscriber_form_empty_follows_plan(app):
    with app.test_request_context("/admin/radius/users", method="POST", data={
            "username": "u1", "password": "p", "status": "enabled",
            "daily_quota_gb": "-3"}):
        from app.radius.routes import users as users_routes
        dto = users_routes._form_dto()
    assert dto.daily_quota_mb == 0


def test_subscriber_value_round_trips_through_the_repo(app):
    with app.app_context():
        from dataclasses import replace
        from app.radius.db.repos import subscribers_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        saved = subscribers_repo.upsert_subscriber(
            _sub(id=None, username="dq1", daily_quota_mb=4096))
        got = subscribers_repo.get_subscriber(1, "dq1")
        assert got.daily_quota_mb == 4096
        subscribers_repo.upsert_subscriber(replace(got, remark="x"))
        assert subscribers_repo.get_subscriber(1, "dq1").daily_quota_mb == 4096
        assert saved is not None


def test_subscriber_form_template_has_the_field():
    import pathlib
    html = pathlib.Path("app/templates/radius/users_form.html").read_text(
        encoding="utf-8")
    assert 'name="daily_quota_gb"' in html


# ─────────────── العرض: المقدارُ قالبٌ يُبصَم في كلّ كرت ───────────────

def _mk_plan():
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo, tenants_repo
    tenants_repo.ensure_default_tenant()
    return plans_repo.upsert_plan(AccessPlan(
        id=None, tenant_id=1, name="OFFER-PLAN", plan_type="quota",
        speed_down_kbps=8000, speed_up_kbps=4000,
        daily_combined_quota_mb=1024))


def test_offer_stores_and_updates_the_amount(app):
    with app.app_context():
        from app.radius.services.card_offers import CardOffersService
        plan = _mk_plan()
        svc = CardOffersService(tenant_id=1)
        offer = svc.create_offer(name="عرض 3 جيجا", duration_minutes=1440,
                                 wholesale=1, selling=2, plan_id=plan.id,
                                 daily_quota_mb=3072)
        assert offer["daily_quota_mb"] == 3072
        kept = svc.update_offer(offer["id"], name="عرض 3 جيجا!")
        assert kept["daily_quota_mb"] == 3072              # لا يُمسح بحفظٍ آخر
        after = svc.update_offer(offer["id"], daily_quota_mb=0)
        assert after["daily_quota_mb"] == 0                # رجوعٌ للباقة


def test_offer_form_parses_gigabytes(app):
    with app.test_request_context("/admin/radius/cards/offers", method="POST",
                                  data={"daily_quota_gb": "1.5"}):
        from app.radius.routes import cards as cards_routes
        assert cards_routes._form_gb_mb("daily_quota_gb") == 1536


def test_generated_cards_carry_the_offer_amount(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import quota_period as qp
        from app.radius.services.cards import get_cards_service
        plan = _mk_plan()
        _batch, cards = get_cards_service().generate_batch(
            actor="tester", plan_id=plan.id, count=2,
            package_name="دفعة 3 جيجا", daily_quota_mb=3072)
        for card in cards:
            sub = subscribers_repo.get_subscriber(1, card.username)
            assert sub.daily_quota_mb == 3072
            assert qp.sub_window_caps(sub, plan)["daily"]["combined"] == 3072


def test_generated_cards_without_amount_follow_the_plan(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import quota_period as qp
        from app.radius.services.cards import get_cards_service
        plan = _mk_plan()
        _batch, cards = get_cards_service().generate_batch(
            actor="tester", plan_id=plan.id, count=1, package_name="عاديّة")
        sub = subscribers_repo.get_subscriber(1, cards[0].username)
        assert sub.daily_quota_mb == 0
        assert qp.sub_window_caps(sub, plan)["daily"]["combined"] == 1024


def test_offers_template_has_the_field_in_both_forms():
    import pathlib
    html = pathlib.Path("app/templates/radius/cards_offers.html").read_text(
        encoding="utf-8")
    assert html.count('name="daily_quota_gb"') >= 2         # إنشاء + تعديل
