# -*- coding: utf-8 -*-
"""مدّة العرض تُعرَض بالوحدة الطبيعية (ساعات/أيام) لا بالدقائق الخام: عرض 3 ساعات
يظهر «3 ساعات» لا «180 د»، و30 يوم يظهر بالأيام لا «43200 د». (ملف واحد.)"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "dur_disp.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.path.join(tmp_path, "dur_disp.db"))
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return a


def test_offer_duration_renders_in_natural_units(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.plans import get_plans_service
        svc = get_plans_service()
        svc.create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="عرض ثلاث ساعات", plan_type="time",
            duration_minutes=180))          # 3 ساعات
        svc.create(actor="root", plan=AccessPlan(
            id=None, tenant_id=1, name="عرض شهر", plan_type="time",
            duration_minutes=30 * 24 * 60))  # 30 يوم = 43200 دقيقة

    with app.test_client() as client:
        with client.session_transaction() as s:
            s["admin_id"] = 1; s["is_super_admin"] = True; s["tenant_id"] = 1
            s["_csrf_token"] = "x"
        html = client.get("/admin/radius/plans").get_data(as_text=True)

    # 180 دقيقة ⟶ «3 ساعات» (لا «180 د»).
    assert "3 ساعات" in html
    assert "180 د" not in html
    # 43200 دقيقة ⟶ أيام (لا «43200 د»).
    assert "43200 د" not in html
    from app.radius.core.duration_fmt import fmt_base_time_ar
    assert fmt_base_time_ar(43200 * 60)[0] in html   # «30 يومًا»
