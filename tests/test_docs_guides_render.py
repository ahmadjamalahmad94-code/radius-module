"""feat/howto-guides-complete — كل أدلة «كيف تستخدمني» تُصيَّر بلا أخطاء.

يتحقّق: الهبّ يسرد الأقسام، كل قسم يفتح، وكل دليل «جاهز» (ready) يُصيَّر 200
بهيكل الأدلة الموحّد (dg-layout) وبعربية. مصدر القائمة CATEGORIES نفسه.
شغّل الملف وحده.
"""
from __future__ import annotations

import os
import re

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "docs.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def _client(app_ctx):
    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["tenant_id"] = 1
        s["admin_id"] = 1
        s["admin_name"] = "tester"
        s["is_super_admin"] = True
        s["_csrf_token"] = "tok"
    return c


_ARABIC = re.compile(r"[؀-ۿ]")


def _ready_pages():
    from app.radius.routes.docs_center import CATEGORIES
    out = []
    for cat_slug, cat in CATEGORIES.items():
        for p in cat["pages"]:
            if p.get("ready"):
                out.append((cat_slug, p["slug"]))
    return out


def test_hub_renders_and_lists_categories(app_ctx):
    from app.radius.routes.docs_center import CATEGORIES
    html = _client(app_ctx).get("/admin/radius/docs").get_data(as_text=True)
    assert "كيف تستخدمني" in html or "الأدلة" in html
    # كل قسم له بطاقة (نتحقّق من ظهور عناوين الأقسام الجديدة على الأقل)
    for title in ("البداية والترخيص", "الأمان والتحكّم", "الباقات والسرعات"):
        assert title in html, f"قسم مفقود من الهبّ: {title}"


def test_all_sections_open(app_ctx):
    from app.radius.routes.docs_center import CATEGORIES
    c = _client(app_ctx)
    for slug, cat in CATEGORIES.items():
        if not cat["pages"]:
            continue
        r = c.get(f"/admin/radius/docs/section/{slug}")
        assert r.status_code == 200, f"قسم لا يفتح: {slug} ({r.status_code})"


# الأدلة الجديدة في هذه الدفعة (تستخدم النمط المشترك docs_guide.css).
_NEW_SLUGS = {
    "getting-started", "provider-license", "packages-subscription",
    "customer-portal", "service-requests", "plans-speeds",
    "store-support", "card-users", "finance-collection", "sessions-report",
    "add-router", "mikrotik-operations", "radius", "hotspot",
    "access-control", "anti-mac-clone",
    "admins", "message-templates", "backups", "alerts",
}


# «سياسات الشبكة» لم تعد دليلًا/صفحة مستقلّة — نُقلت إلى لوحة الراوتر
# (commit 80e9483) وطُويت داخل دليل «إعداد وتشغيل المايكروتيك».
_REMOVED_SLUGS = {"network-policies"}


@pytest.mark.parametrize("cat_slug,slug", _ready_pages())
def test_ready_guide_renders(app_ctx, cat_slug, slug):
    """كل دليل جاهز (قديم أو جديد) يُصيَّر 200 بهيكل الأدلة الموحّد وبعربية."""
    html = _client(app_ctx).get(f"/admin/radius/docs/{slug}").get_data(as_text=True)
    assert "dg-layout" in html, f"دليل {slug}: هيكل الأدلة غائب"
    assert "data-dg-toc" in html, f"دليل {slug}: لا فهرس جانبي"
    assert _ARABIC.search(html), f"دليل {slug}: لا عربية"
    # الأدلة الجديدة تربط النمط المشترك (DRY)؛ القديمة تُبقي نمطها المضمّن.
    if slug in _NEW_SLUGS:
        assert "docs_guide.css" in html, f"دليل {slug}: نمط الأدلة المشترك غير مربوط"
        assert "docs_guide.js" in html, f"دليل {slug}: سكربت الفهرس غير مربوط"


def test_new_guides_all_ready_and_reachable(app_ctx):
    """كل أدلة هذه الدفعة موجودة في CATEGORIES، جاهزة، وتفتح 200."""
    ready = {slug for _c, slug in _ready_pages()}
    missing = _NEW_SLUGS - ready
    assert not missing, f"أدلة جديدة غير مُسجَّلة/غير جاهزة: {missing}"
    c = _client(app_ctx)
    for slug in _NEW_SLUGS:
        assert c.get(f"/admin/radius/docs/{slug}").status_code == 200, slug


def test_count_ready_guides(app_ctx):
    # تأكيد أنّ التغطية اتسعت فعليًّا (≥ 35 دليلًا جاهزًا).
    assert len(_ready_pages()) >= 35


def test_network_policies_not_standalone(app_ctx):
    """«سياسات الشبكة» أُزيلت كدليل مستقلّ: لا في CATEGORIES، ولا تفتح، ولا في الهبّ."""
    from app.radius.routes.docs_center import CATEGORIES
    all_slugs = {p["slug"] for cat in CATEGORIES.values() for p in cat["pages"]}
    for slug in _REMOVED_SLUGS:
        assert slug not in all_slugs, f"دليل مُزال ما زال مُسجَّلًا: {slug}"
    c = _client(app_ctx)
    # الراوت يردّ 404 لأن الدليل غير موجود/غير جاهز
    assert c.get("/admin/radius/docs/network-policies").status_code == 404
    # لا يظهر عنوانه المستقلّ في الهبّ
    hub = c.get("/admin/radius/docs").get_data(as_text=True)
    assert "دليل: سياسات الشبكة" not in hub


def test_network_policies_folded_into_mikrotik_ops(app_ctx):
    """محتوى سياسات الشبكة (حظر/سماح المواقع) مطويّ داخل دليل عمليات المايكروتيك."""
    html = _client(app_ctx).get("/admin/radius/docs/mikrotik-operations").get_data(as_text=True)
    assert "سياسات الشبكة" in html
    assert "حظر المواقع" in html
    assert "المواقع المسموحة" in html or "walled-garden" in html
