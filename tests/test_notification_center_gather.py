# -*- coding: utf-8 -*-
"""«مركز الإشعارات = جامع كل شيء» — إكمال جمع كل سطوح الإشعارات تحت المجموعة.

يثبّت:
  • شريط تنقّل مركز الإشعارات (notifications-nav) يظهر في كل صفحات المركز
    الأربع ويصل لكل سطوح الإشعارات (المركز/التكاملات/الإدارة/المشتركين +
    التنبيهات الذكية المُسطَّحة من «الشبكة»).
  • «التكاملات والقنوات» يُسطِّح صفحات تهيئة القنوات الكاملة (واتساب،
    قنوات SMS، بوت واتساب، الرصيد) — فلا تبقى محصورة في «التواصل والحملات».
  • الخلفيّات المشتركة (communications_channels/bot/quota، whatsapp) ما زالت
    تعمل (load-bearing لـintegrations_hub) — لا حذف.
  • لا صفحة إشعارات يتيمة: الصفحات المطويّة تُعيد التوجيه للمركز.

شغّل الملف وحده (عزل لكل ملف)."""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "gather.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        # مدير سوبر حقيقي في القاعدة — مركز الإشعارات (الجرس) يستدعي
        # current_admin()، و/alerts محروسة بـrequires_perm فتحلّ السوبر من
        # صف القاعدة لا من علم الجلسة وحده.
        from app.radius.db.connection import transaction
        with transaction() as cx:
            cx.execute(
                "INSERT OR REPLACE INTO admins(id,username,password_hash,"
                "full_name,is_super_admin,enabled,created_at) "
                "VALUES(1,'t','x','t',1,1,'2026-01-01')")
        yield application


def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


# صفحات المركز الأربع التي يجب أن تحمل شريط التنقّل الموحّد.
CENTER_PAGES = [
    "/admin/radius/notifications",
    "/admin/radius/integrations",
    "/admin/radius/admin-notifications",
    "/admin/radius/subscriber-notifications",
]

# كل وجهات سطوح الإشعارات التي يجب أن يصلها شريط التنقّل.
NAV_TARGETS = [
    "/admin/radius/notifications",
    "/admin/radius/integrations",
    "/admin/radius/admin-notifications",
    "/admin/radius/subscriber-notifications",
    "/admin/radius/alerts",          # التنبيهات الذكية (مُسطَّحة من الشبكة)
]


# ════════════ (1) شريط التنقّل الموحّد في كل صفحات المركز ════════════
class TestCenterNav:
    @pytest.mark.parametrize("url", CENTER_PAGES)
    def test_every_center_page_has_nav(self, app, url):
        c = _client(app)
        html = c.get(url).get_data(as_text=True)
        assert 'data-testid="notifications-nav"' in html, url

    @pytest.mark.parametrize("url", CENTER_PAGES)
    def test_nav_links_every_notification_surface(self, app, url):
        c = _client(app)
        html = c.get(url).get_data(as_text=True)
        for target in NAV_TARGETS:
            assert f'href="{target}"' in html, f"{url} missing nav link {target}"

    def test_smart_alerts_reachable_from_center(self, app):
        # «التنبيهات الذكية» كانت تُبلَغ فقط من «الشبكة»؛ الآن مُسطَّحة في
        # شريط مركز الإشعارات فتُصبح مبلوغة من المركز أيضًا.
        c = _client(app)
        html = c.get("/admin/radius/integrations").get_data(as_text=True)
        assert 'href="/admin/radius/alerts"' in html
        assert "التنبيهات الذكية" in html
        # والصفحة نفسها ما زالت تعمل
        assert c.get("/admin/radius/alerts").status_code == 200


# ════════════ (2) تسطيح صفحات القنوات الكاملة من المركز ════════════
class TestChannelFullPagesSurfaced:
    def test_integrations_links_full_channel_pages(self, app):
        c = _client(app)
        html = c.get("/admin/radius/integrations").get_data(as_text=True)
        # روابط الصفحات الكاملة لكل قناة — مُسطَّحة من «التواصل والحملات».
        for target in (
            "/admin/radius/whatsapp",                 # واتساب الكاملة (حالة+اختبار)
            "/admin/radius/communications/bot",       # بوت واتساب
            "/admin/radius/communications/channels",  # قنوات الإرسال (SMS)
            "/admin/radius/communications/quota",     # الرصيد والحِزم
        ):
            assert f'href="{target}"' in html, f"integrations missing link {target}"


# ════════════ (3) الخلفيّات المشتركة ما زالت تعمل (load-bearing) ════════════
class TestSharedBackendsIntact:
    @pytest.mark.parametrize("url", [
        "/admin/radius/whatsapp",
        "/admin/radius/communications/channels",
        "/admin/radius/communications/bot",
        "/admin/radius/communications/quota",
    ])
    def test_channel_backend_pages_render(self, app, url):
        c = _client(app)
        assert c.get(url).status_code == 200, url

    def test_integrations_still_posts_to_shared_endpoints(self, app):
        # نماذج «التكاملات» تنشر لنقاط الحفظ القائمة (لا تكرار منطق).
        c = _client(app)
        html = c.get("/admin/radius/integrations").get_data(as_text=True)
        assert "whatsapp/settings" in html         # action واتساب
        assert "communications/channels" in html    # action SMS
        assert "wh_settings" in html or "/webhooks" in html  # action ويبهوك


# ════════════ (4) لا صفحة إشعارات يتيمة — الطيّ يُعيد التوجيه ════════════
class TestNoOrphans:
    @pytest.mark.parametrize("old,dest", [
        ("/admin/radius/alerts/telegram", "/admin/radius/admin-notifications"),
        ("/admin/radius/network/telegram", "/admin/radius/integrations"),
        ("/admin/radius/communications/notifications",
         "/admin/radius/subscriber-notifications"),
    ])
    def test_folded_pages_redirect(self, app, old, dest):
        c = _client(app)
        r = c.get(old, follow_redirects=False)
        assert r.status_code in (301, 302), old
        assert dest in r.headers.get("Location", ""), old
