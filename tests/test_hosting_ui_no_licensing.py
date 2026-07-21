"""MT35 — وضع الاستضافة المفتوحة: لا أثر للتراخيص، ولوحة المزوّد هي الهبوط.

المالك رأى في نسخته المنشورة: مسار الجذر يفتح **لوحة الريديوس** (مشتركون
وكروت لا تلزمه)، وقسم «التكامل والجسر» في القائمة على كل صفحة، وصفحة
«ترخيص النظام» تُفتح كاملةً بالعنوان المباشر، ونصوص «لوحة التراخيص» في
«حسابي» و«النسخ الاحتياطي». هذه الاختبارات تُغلق ذلك وتَمنع رجوعه.
"""
from __future__ import annotations

import os

import pytest

LICENSE_MARKERS = ("لوحة التراخيص", "ترخيص النظام", "التكامل والجسر",
                   "جسر الإدارة", "حالة منح المزوّد", "ربط وتفعيل النسخة")


def _make_app(monkeypatch, tmp_path, *, hosting: bool):
    db_file = os.path.join(tmp_path, f"ui_{int(hosting)}.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    if hosting:
        monkeypatch.setenv("HOBERADIUS_OPEN_HOSTING", "1")
    else:
        monkeypatch.delenv("HOBERADIUS_OPEN_HOSTING", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.ensure_bootstrap_admin()
    return app


def _owner_client(app):
    c = app.test_client()
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.admins import get_admins_service
        a = admins_repo.get_admin(1)
        perms = list(get_admins_service().permissions_of(a))
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user=a.username, admin_name=a.full_name,
                 is_super_admin=True, tenant_id=1, permissions=perms)
    return c


@pytest.fixture
def hosting_app(monkeypatch, tmp_path):
    return _make_app(monkeypatch, tmp_path, hosting=True)


# ─────────────── هبوط المالك ───────────────

def test_root_dashboard_redirects_owner_to_provider_panel(hosting_app):
    """المالك على الجذر لا يهبط في لوحة ريديوس فارغة — بل في لوحته."""
    c = _owner_client(hosting_app)
    r = c.get("/admin/radius/")
    assert r.status_code == 302, f"لم يُحوَّل (http={r.status_code})"
    assert "/provider" in r.headers.get("Location", "")


def test_owner_still_sees_radius_panel_inside_a_network(monkeypatch, tmp_path):
    """التحويل لا يَكسر دعم المالك: داخل مسار شبكة تظهر لوحة الريديوس."""
    app = _make_app(monkeypatch, tmp_path, hosting=True)
    with app.app_context():
        from app.radius.core.tenant import Tenant
        from app.radius.db.repos import tenants_repo
        from app.radius.middleware.tenant_path import invalidate_slug_cache
        from app.radius.services.tenants import get_tenants_service
        get_tenants_service().create_trial(
            actor="p", tenant=Tenant(id=None, slug="netx", name="X",
                                     display_name="X", status="trial"),
            trial_days=30, operator_username="netx-own", operator_password="pw12345678")
        invalidate_slug_cache()
    c = _owner_client(app)
    r = c.get("/netx/admin/radius/")
    assert r.status_code == 200, f"المالك لا يصل لوحة الشبكة للدعم (http={r.status_code})"


# ─────────────── إخفاء التراخيص ───────────────

@pytest.mark.parametrize("path", [
    "/admin/radius/license-file",
    "/admin/radius/admin-bridge",
    "/admin/radius/_license/connect",
    "/admin/radius/_provider/grants",
])
def test_licensing_pages_are_gone(hosting_app, path):
    """404 لا 403: لا شيء محجوب — لا شيء موجود أصلًا."""
    c = _owner_client(hosting_app)
    assert c.get(path).status_code == 404, f"{path}: ما زالت مفتوحة"


@pytest.mark.parametrize("path", [
    "/admin/radius/license-file/config",
    "/admin/radius/license-file/sync",
    "/admin/radius/_license/connect/link",
])
def test_licensing_write_endpoints_are_gone(hosting_app, path):
    """نقاط الكتابة (POST فقط) — الحارس يُغلقها هي الأخرى."""
    c = _owner_client(hosting_app)
    # نجلب صفحةً أوّلًا كي يُولَّد رمز CSRF في الجلسة، وإلّا اعترض فحص
    # الـCSRF قبل الحارس فبدا الاختبار ناجحًا لسببٍ خاطئ.
    c.get("/admin/radius/account")
    with c.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    assert tok, "لم يُولَّد رمز CSRF — الاختبار سيقيس الشيء الخطأ"
    r = c.post(path, data={"_csrf_token": tok})
    assert r.status_code == 404, f"{path}: ما زالت تقبل الكتابة (http={r.status_code})"


@pytest.mark.parametrize("path", [
    "/admin/radius/account", "/admin/radius/backups",
    "/admin/radius/subscribers", "/admin/radius/plans", "/admin/radius/settings",
])
def test_no_licensing_text_anywhere(hosting_app, path):
    c = _owner_client(hosting_app)
    r = c.get(path)
    assert r.status_code == 200, f"{path}: http={r.status_code}"
    body = r.get_data(as_text=True)
    leaks = [m for m in LICENSE_MARKERS if m in body]
    assert not leaks, f"{path}: بقايا تراخيص → {leaks}"


def test_network_admin_pages_are_licence_free(monkeypatch, tmp_path):
    """مدير الشبكة لا يرى شيئًا من التراخيص أيضًا."""
    import re
    app = _make_app(monkeypatch, tmp_path, hosting=True)
    with app.app_context():
        from app.radius.core.tenant import Tenant
        from app.radius.middleware.tenant_path import invalidate_slug_cache
        from app.radius.services.tenants import get_tenants_service
        get_tenants_service().create_trial(
            actor="p", tenant=Tenant(id=None, slug="nety", name="Y",
                                     display_name="Y", status="trial"),
            trial_days=30, operator_username="nety-own", operator_password="pw12345678")
        invalidate_slug_cache()
    c = app.test_client()
    html = c.get("/nety/admin/radius/login").get_data(as_text=True)
    tok = re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)
    c.post("/nety/admin/radius/login",
           data={"username": "nety-own", "password": "pw12345678", "_csrf_token": tok})
    body = c.get("/nety/admin/radius/").get_data(as_text=True)
    leaks = [m for m in LICENSE_MARKERS if m in body]
    assert not leaks, f"لوحة الشبكة تعرض → {leaks}"


# ─────────────── النسخة المرخّصة لا تتأثّر ───────────────

def test_licensed_build_keeps_everything(monkeypatch, tmp_path):
    """بلا HOBERADIUS_OPEN_HOSTING تبقى صفحات التراخيص كما هي — التغيير
    مشروطٌ بوضع الاستضافة لا حذفٌ للميزة."""
    app = _make_app(monkeypatch, tmp_path, hosting=False)
    c = _owner_client(app)
    assert c.get("/admin/radius/license-file").status_code == 200
    r = c.get("/admin/radius/")
    assert r.status_code == 200, "النسخة المرخّصة حُوِّلت للوحة المزوّد بالخطأ"
