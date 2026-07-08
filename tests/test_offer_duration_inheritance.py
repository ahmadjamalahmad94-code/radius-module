# -*- coding: utf-8 -*-
"""«مدة الوقت» على العرض (plan.duration_minutes) — الحقل الزمنيّ الموحَّد جوار
«النوع»، ووراثته المزدوجة:

  1) الحقل يُعرَض جوار «النوع» بوحدات دقيقة/ساعة/يوم (unit picker).
  2) عرض بطاقة: «8 ساعات» ⟶ رصيد وقت البطاقة المولَّدة = 8 ساعات (موروث).
  3) عرض مشترك: سعر 80 + مدة 30 يوم ⟶ دفع 80 يمدّد الصلاحية 30 يومًا،
     ودفع 160 يمدّدها 60 يومًا (تناسبيًّا مع السعر، مع التراكم).

يعمل في CI (لا بيانات عميل). شغّل هذا الملف وحده."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest


def db():
    from app.radius.db.connection import db as live_db
    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "offer_dur.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


def _login_super(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "root"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"


# ── 1) الحقل يُعرَض جوار «النوع» بوحدات دقيقة/ساعة/يوم ───────────────────

def test_duration_field_renders_next_to_type_with_min_hr_day_units(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.plans import get_plans_service
        from flask import url_for
        p = get_plans_service().create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="4 ميجا", plan_type="time",
            duration_minutes=480, price=10.0))
        with app.test_request_context():
            edit_url = url_for("radius.plans_edit", plan_id=p.id)

    with app.test_client() as client:
        _login_super(client)
        html = client.get(edit_url).get_data(as_text=True)

    # الحقل الموحَّد موجود بعنوانه الجديد وحاويته.
    assert "مدة الوقت" in html
    assert "data-pl-duration-row" in html
    # يقع جوار «النوع» (بعد select الـ plan_type مباشرةً، قبل «نوع الخدمة»).
    i_type = html.find('name="plan_type"')
    i_dur = html.find("data-pl-duration-row")
    i_svc = html.find('name="service_type"')
    assert 0 < i_type < i_dur < i_svc, (i_type, i_dur, i_svc)
    # منتقي الوحدات يعرض دقيقة/ساعة/يوم (لا شهر) ضمن صفّ المدة (حتى «نوع الخدمة»).
    row = html[i_dur:i_svc]
    assert 'value="min"' in row and 'value="hr"' in row and 'value="day"' in row
    assert 'value="month"' not in row
    # حقل الإدخال الأساسيّ لا يزال duration_minutes (لا حقل مكرَّر).
    assert html.count('name="duration_minutes"') == 1


# ── 2) عرض بطاقة: «8 ساعات» ⟶ رصيد وقت البطاقة = 8 ساعات (موروث) ─────────

def test_card_inherits_offer_duration_as_time_budget(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.plans import get_plans_service
        from app.radius.services.cards import get_cards_service
        from app.radius.services import card_accounting

        # عرض بطاقة: «مدة الوقت» = 8 ساعات (480 دقيقة). لا نافذة وقت صريحة.
        plan = get_plans_service().create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="بطاقة 8 ساعات", plan_type="time",
            duration_minutes=8 * 60))

        batch, cards = get_cards_service().generate_batch(
            actor="root", plan_id=plan.id, count=1, package_name="P8H")

        # الحزمة ورثت نافذة العرض صراحةً = 8 ساعات.
        assert batch.time_value == 8 and batch.time_unit == "hours", (
            batch.time_value, batch.time_unit)

        # رصيد وقت البطاقة (من أوّل اتصال) = 8 ساعات بالثواني.
        budget = card_accounting.budget_seconds(
            validity_after_first_login_days=getattr(
                batch, "validity_after_first_login_days", 0),
            time_value=batch.time_value, time_unit=batch.time_unit,
            duration_minutes=int(plan.duration_minutes),
            validity_days=int(plan.validity_days or 0),
        )
        assert budget == 8 * 3600, budget
        assert len(cards) == 1


def test_card_generation_does_not_override_explicit_window(app):
    """قيمة وقت صريحة من الشاشة تبقى مُقدَّمة على وراثة العرض."""
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.plans import get_plans_service
        from app.radius.services.cards import get_cards_service
        plan = get_plans_service().create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="عرض مزدوج", plan_type="time",
            duration_minutes=8 * 60))
        batch, _ = get_cards_service().generate_batch(
            actor="root", plan_id=plan.id, count=1, package_name="EXP",
            time_value=3, time_unit="days")
        assert batch.time_value == 3 and batch.time_unit == "days"


# ── 3) عرض مشترك: سعر 80 + مدة 30 يوم ⟶ دفع 80 = +30 يوم، 160 = +60 يوم ──

def _subscriber_offer_plan():
    from app.radius.core.types import AccessPlan
    from app.radius.services.plans import get_plans_service
    # 30 يوم = 43200 دقيقة، بسعر 80.
    return get_plans_service().create(actor="root", plan=AccessPlan(
        id=None, tenant_id=1, name="4 ميجا شهريّ", plan_type="recurring",
        duration_minutes=30 * 24 * 60, price=80.0, currency="ILS"))


def _make_sub(username, plan_id):
    from app.radius.core.types import Subscriber
    from app.radius.services.users import get_users_service
    get_users_service().create(actor="root", sub=Subscriber(
        id=None, username=username, password="x", tenant_id=1, plan_id=plan_id))


def _pay(username, amount):
    from app.radius.services.accounting import AccountingService
    return AccountingService(tenant_id=1).create_payment(
        {"username": username, "amount": amount, "apply_to_radius": True},
        actor="root")


def _naive(iso: str) -> datetime:
    """Parse an ISO expiry and drop tzinfo so it compares to utcnow() naively."""
    return datetime.fromisoformat(iso).replace(tzinfo=None)


def test_subscriber_offer_pay_price_extends_by_duration(app):
    with app.app_context():
        plan = _subscriber_offer_plan()
        _make_sub("subA", plan.id)
        _make_sub("subB", plan.id)

        # دفع سعر العرض كاملاً (80) ⟶ +30 يوم.
        now = datetime.utcnow()
        pay1 = _pay("subA", 80)
        assert pay1["proportional_activation"]["earned_minutes"] == 30 * 24 * 60
        assert pay1["activation_result"]["applied_to_radius"] is True
        exp1 = _naive(pay1["activation_result"]["new_expire_at"])
        assert abs((exp1 - (now + timedelta(days=30))).total_seconds()) < 120

        # دفع ضعف السعر (160) ⟶ +60 يوم (تناسبيّ).
        now = datetime.utcnow()
        pay2 = _pay("subB", 160)
        assert pay2["proportional_activation"]["earned_minutes"] == 60 * 24 * 60
        exp2 = _naive(pay2["activation_result"]["new_expire_at"])
        assert abs((exp2 - (now + timedelta(days=60))).total_seconds()) < 120


def test_subscriber_offer_payments_stack(app):
    """دفعتان بسعر العرض تتراكمان: 80 ثم 80 ⟶ +60 يومًا إجمالًا."""
    with app.app_context():
        plan = _subscriber_offer_plan()
        _make_sub("subC", plan.id)
        now = datetime.utcnow()
        _pay("subC", 80)
        pay2 = _pay("subC", 80)
        exp = _naive(pay2["activation_result"]["new_expire_at"])
        assert abs((exp - (now + timedelta(days=60))).total_seconds()) < 120
