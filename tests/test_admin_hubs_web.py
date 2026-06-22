"""Administration hubs consolidation (A1–A3).

UI-only: the 12 administration sidebar entries collapse into 3 hub entries
(المدراء والموزعون / الأدوار والصلاحيات / البيانات والحفظ والأرشفة) plus the
sensitive system entries (settings / sync / tenants) which stay standalone.
Each hub page gains a shared in-section nav. The dangerous POST actions
(backup restore / lifecycle run / recycle restore) keep their own
confirmation-gated endpoints — nothing about them is touched.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "admin_hub.db")
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

        run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "admin_hub"
        sess["admin_name"] = "Admin Hub"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "admin-csrf"


_PAGES = {
    "admin-operations-nav": [
        "/admin/radius/business-operators",
        "/admin/radius/admins",
        "/admin/radius/admins/profile-summary",
        "/admin/radius/distributors",
    ],
    # /permissions is permission-gated (PERM_AUDIT_VIEW); the roles page
    # already carries the shared roles-permissions nav, so it covers the
    # nav assertion without weakening the gate.
    "roles-permissions-nav": [
        "/admin/radius/roles",
    ],
    "data-protection-nav": [
        "/admin/radius/backups",
        "/admin/radius/recycle-bin",
        "/admin/radius/lifecycle",
    ],
}


def test_every_admin_hub_page_renders_with_its_nav(app):
    with app.test_client() as client:
        _auth(client)
        for nav, urls in _PAGES.items():
            for url in urls:
                res = client.get(url)
                assert res.status_code == 200, url
                assert f'data-testid="{nav}"' in res.get_data(as_text=True), (url, nav)


def test_sidebar_collapsed_to_three_admin_hubs_plus_system(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/").get_data(as_text=True)
    # ملاحظة (chore/customer-panel-cleanup-1، يونيو 2026): «طابور المزامنة»
    # أُزيل من الشريط الجانبي (طابور router-push خامل بعد إسقاط mikrotik_configs)
    # فلم يَعُد ضمن البنود المتوقّعة. «المستأجرون» باقٍ لكن للسوبر فقط — وهذا
    # الاختبار يُصادق سوبر (انظر _auth) فيَظهر له.
    for label in ("المدراء والموزعون", "الأدوار والصلاحيات",
                  "البيانات والحفظ والأرشفة",
                  "إعدادات النظام", "المستأجرون"):
        assert label in html, label
    # «طابور المزامنة» مُزال نهائيًّا من الشريط (لا للسوبر أيضًا).
    assert "طابور المزامنة" not in html
    # sub-labels now live in the in-section navs, not the sidebar
    assert "ملخص المدراء" not in html
    assert "سلة المحذوفات" not in html
    assert "مركز الصلاحيات" not in html


def test_sensitive_post_endpoints_stay_standalone(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/backups/restore" in rules
    assert "/admin/radius/lifecycle/run" in rules
    assert "/admin/radius/recycle-bin/<entity_type>/<int:entity_id>/restore" in rules
    # the permission matrix stays registered (and permission-gated)
    assert "/admin/radius/permissions" in rules
