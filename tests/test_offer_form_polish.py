# -*- coding: utf-8 -*-
"""صقل نموذج العرض (plans_form): الأولوية 1–10 (ترتيب عرض فقط)، إزالة تكرار
مفاتيح «بوابة الدخول/PPP» (نوع الخدمة هو المصدر الوحيد)، وعرض السرعات بالميجا
في بطاقات قائمة العروض.

يعمل في CI (لا بيانات عميل). شغّل هذا الملف وحده."""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "offer_polish.db")
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


def _create(client, **form):
    from flask import url_for
    body = {"_csrf_token": "off-csrf", "plan_type": "time"}
    body.update(form)
    return client.post("/admin/radius/plans", data=body, follow_redirects=False)


def _plan_by_name(name):
    from app.radius.services.plans import get_plans_service
    for p in get_plans_service().list(limit=500):
        if p.name == name:
            return p
    return None


# ── 1) الأولوية: تُقيَّد 1–10 عند الحفظ (ترتيب عرض فقط) ──────────────────

def test_priority_clamped_to_1_10_on_save(app):
    with app.test_client() as client:
        _login_super(client)
        # قيمة فوق المدى (100) → تُقصّ إلى 10.
        assert _create(client, name="عرض عالي", priority="100",
                       service_type="Hotspot").status_code == 302
        # قيمة تحت المدى (0) → تُرفع إلى 1.
        assert _create(client, name="عرض منخفض", priority="0",
                       service_type="Hotspot").status_code == 302
        # قيمة سليمة (7) تبقى كما هي.
        assert _create(client, name="عرض وسط", priority="7",
                       service_type="Hotspot").status_code == 302

    with app.app_context():
        assert _plan_by_name("عرض عالي").priority == 10
        assert _plan_by_name("عرض منخفض").priority == 1
        assert _plan_by_name("عرض وسط").priority == 7


def test_priority_input_constrained_in_form(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.plans import get_plans_service
        from flask import url_for
        p = get_plans_service().create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="عرض", plan_type="time", priority=100))
        with app.test_request_context():
            edit_url = url_for("radius.plans_edit", plan_id=p.id)
    with app.test_client() as client:
        _login_super(client)
        html = client.get(edit_url).get_data(as_text=True)
    # الحقل مُقيَّد 1–10، والقيمة القديمة (100) تُعرَض مقصوصة إلى 10.
    i = html.find('name="priority"')
    seg = html[i - 120:i + 120]
    assert 'min="1"' in seg and 'max="10"' in seg
    assert 'value="10"' in seg


# ── 2) إزالة تكرار مفاتيح الخدمة — «نوع الخدمة» هو المصدر الوحيد ──────────

def test_service_toggles_removed_and_type_drives_enablement(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.plans import get_plans_service
        from flask import url_for
        p = get_plans_service().create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="عرض خدمة", plan_type="time"))
        with app.test_request_context():
            edit_url = url_for("radius.plans_edit", plan_id=p.id)

    with app.test_client() as client:
        _login_super(client)
        html = client.get(edit_url).get_data(as_text=True)
        # لم تعد مفاتيح «بوابة الدخول/PPP» تُعرَض في «خدمات الاتصال».
        assert 'name="hotspot_enabled"' not in html
        assert 'name="ppp_enabled"' not in html
        # «نوع الخدمة» ما زال المصدر الوحيد.
        assert 'name="service_type"' in html

        # اختيار «الاثنين» (هوت سبوت + برودباند) يُفعّل الحقلين على الحفظ.
        _create(client, name="عرض الاثنين", plan_type="time",
                service_type=["Hotspot", "PPPoE"])
        # هوت سبوت فقط.
        _create(client, name="عرض هوتسبوت", plan_type="time",
                service_type="Hotspot")
        # برودباند فقط.
        _create(client, name="عرض برودباند", plan_type="time",
                service_type="PPPoE")

    with app.app_context():
        both = _plan_by_name("عرض الاثنين")
        assert both.hotspot_enabled is True and both.ppp_enabled is True
        assert both.service_type == "Both"
        hs = _plan_by_name("عرض هوتسبوت")
        assert hs.hotspot_enabled is True and hs.ppp_enabled is False
        pp = _plan_by_name("عرض برودباند")
        assert pp.hotspot_enabled is False and pp.ppp_enabled is True


# ── 3) السرعات بالميجا في بطاقات القائمة ─────────────────────────────────

def test_offer_card_speeds_render_in_mega(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.plans import get_plans_service
        svc = get_plans_service()
        svc.create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="ثمانية ميجا", plan_type="time",
            speed_down_kbps=8192, speed_up_kbps=8192))
        svc.create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="واحد ونصف", plan_type="time",
            speed_down_kbps=1536, speed_up_kbps=1536))
        svc.create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="بلا حد", plan_type="unlimited",
            speed_down_kbps=0, speed_up_kbps=0))

    with app.test_client() as client:
        _login_super(client)
        html = client.get("/admin/radius/plans").get_data(as_text=True)

    # بطاقة العرض: 8192K ⟶ «8↓ / 8↑ ميجا»، والصيغة الخام القديمة زالت.
    assert "8↓ / 8↑" in html
    assert "ميجا" in html
    assert "8192↓ / 8192↑ K" not in html
    # 1536K ⟶ «1.5 ميجا» (خانة عشرية واحدة عند الحاجة).
    assert "1.5↓ / 1.5↑" in html
    # السرعة صفر تبقى «غير محدودة» — لا «0 ميجا» ولا «0↓ / 0↑» في البطاقة.
    assert "0↓ / 0↑" not in html
    assert "0 ميجا" not in html
