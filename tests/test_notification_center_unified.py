# -*- coding: utf-8 -*-
"""Phase 1 — التوحيد الجذري للإشعارات.

يثبّت: المحرّك الموحّد (admin_alerts.dispatch → الجرس دائمًا + تلجرام عبر
المُرسِل القانوني فقط + قنوات لكل حدث)، الصفحات الثلاث + المركز، إعادة توجيه
الصفحات المطويّة/المكرّرة، وحذف المكرّر network_telegram_settings.

شغّل الملف وحده (عزل لكل ملف)."""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "notif.db")
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
        yield application


def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def _sync_capture(monkeypatch):
    sent = []
    from app.radius.services import telegram_notifier, admin_alerts
    monkeypatch.setattr(telegram_notifier, "send_to_tenant",
                        lambda tid, text: (sent.append((tid, text)) or (True, "")))

    class _SyncThread:
        def __init__(self, target=None, **kw): self._t = target
        def start(self): self._t() if self._t else None
    monkeypatch.setattr(admin_alerts.threading, "Thread", _SyncThread)
    return sent


def _configure_bot():
    from app.radius.db.repos import tenant_telegram_settings_repo as repo
    repo.upsert(tenant_id=1, bot_token="123:abc", chat_id="-100", enabled=True)


# ════════════ (1) المحرّك الموحّد ════════════
class TestEngine:
    def test_dispatch_writes_bell_even_without_telegram(self, app, monkeypatch):
        with app.app_context():
            from app.radius.services import admin_alerts as aa
            from app.radius.db.repos import notifications_repo as nr
            sent = _sync_capture(monkeypatch)
            aa.dispatch(1, "subscriber_new", {"full_name": "x", "username": "u",
                        "plan": "p", "mobile": "0", "actor": "a"})
            items = nr.list_for(1, limit=10)
            assert len(items) == 1                    # الجرس كُتب
            assert items[0]["title"]                   # عنوان = تسمية الحدث
            assert sent == []                          # لا تلجرام (غير مضبوط)

    def test_dispatch_sends_telegram_via_canonical_sender(self, app, monkeypatch):
        with app.app_context():
            from app.radius.services import admin_alerts as aa
            from app.radius.db.repos import notifications_repo as nr
            sent = _sync_capture(monkeypatch)
            _configure_bot()
            aa.set_channels(1, "loop_detected", ["bell", "telegram"])
            aa.dispatch(1, "loop_detected", {"router": "r", "interface": "e", "details": "d"})
            assert len(nr.list_for(1, limit=10)) == 1   # الجرس
            assert len(sent) == 1                        # تلجرام عبر المُرسِل القانوني

    def test_bell_only_event_does_not_telegram(self, app, monkeypatch):
        with app.app_context():
            from app.radius.services import admin_alerts as aa
            from app.radius.db.repos import notifications_repo as nr
            sent = _sync_capture(monkeypatch)
            _configure_bot()
            aa.set_channels(1, "loop_detected", ["bell"])      # تلجرام مُطفأ
            aa.dispatch(1, "loop_detected", {"router": "r", "interface": "e", "details": "d"})
            assert len(nr.list_for(1, limit=10)) == 1          # الجرس فقط
            assert sent == []                                   # لا تلجرام

    def test_channels_persist_and_bell_always_included(self, app):
        with app.app_context():
            from app.radius.services import admin_alerts as aa
            aa.set_channels(1, "mac_clone_detected", ["telegram"])  # بلا bell صراحةً
            chans = aa.channels_for(1, "mac_clone_detected")
            assert "bell" in chans and "telegram" in chans          # الجرس يُضاف دائمًا

    def test_catalogue_exposes_channels(self, app):
        with app.app_context():
            from app.radius.services import admin_alerts as aa
            cat = aa.catalogue(1)
            assert cat and all("channels" in it for it in cat)
            assert all("bell" in it["channels"] for it in cat)


# ════════════ (2) الصفحات الثلاث + المركز ════════════
class TestPages:
    def test_three_pages_render(self, app):
        c = _client(app)
        for url in ("/admin/radius/integrations",
                    "/admin/radius/admin-notifications",
                    "/admin/radius/subscriber-notifications"):
            r = c.get(url)
            assert r.status_code == 200, url

    def test_admin_notifications_shows_catalogue_and_channels(self, app):
        c = _client(app)
        html = c.get("/admin/radius/admin-notifications").get_data(as_text=True)
        assert "إشعارات الإدارة" in html
        assert "الجرس" in html and "تلجرام" in html          # chips القنوات
        assert "إضافة مشترك جديد" in html                    # حدث من الجرد

    def test_integrations_has_all_channels(self, app):
        c = _client(app)
        html = c.get("/admin/radius/integrations").get_data(as_text=True)
        for label in ("بوت تلجرام", "واتساب", "رسائل SMS", "Webhook"):
            assert label in html

    def test_set_channels_endpoint_persists(self, app):
        c = _client(app)
        c.get("/admin/radius/admin-notifications")   # mint CSRF
        with c.session_transaction() as s:
            tok = s.get("_csrf_token")
        r = c.post("/admin/radius/admin-notifications/channels",
                   data={"key": "loop_detected", "channels": ["bell", "telegram"],
                         "_csrf_token": tok or ""},
                   headers={"X-CSRFToken": tok or ""})
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True and "telegram" in data["channels"]
        with app.app_context():
            from app.radius.services import admin_alerts as aa
            assert "telegram" in aa.channels_for(1, "loop_detected")

    def test_set_channels_unknown_event_404(self, app):
        c = _client(app)
        c.get("/admin/radius/admin-notifications")   # mint CSRF
        with c.session_transaction() as s:
            tok = s.get("_csrf_token")
        r = c.post("/admin/radius/admin-notifications/channels",
                   data={"key": "no_such_event", "_csrf_token": tok or ""},
                   headers={"X-CSRFToken": tok or ""})
        assert r.status_code == 404


# ════════════ (3) إعادة توجيه الصفحات المطويّة/المكرّرة ════════════
class TestRedirects:
    # الصفحات التي انتقل محتواها كليًّا → إعادة توجيه (لا 404):
    #  • /alerts/telegram → «إشعارات الإدارة» (الجرد + القنوات).
    #  • /network/telegram (المكرّر المحذوف) → «التكاملات والقنوات».
    @pytest.mark.parametrize("old,dest", [
        ("/admin/radius/alerts/telegram", "/admin/radius/admin-notifications"),
        ("/admin/radius/network/telegram", "/admin/radius/integrations"),
    ])
    def test_folded_pages_redirect_to_new(self, app, old, dest):
        c = _client(app)
        r = c.get(old, follow_redirects=False)
        assert r.status_code in (301, 302)
        assert dest in r.headers.get("Location", "")

    def test_whatsapp_and_webhook_surfaced_in_integrations(self, app):
        # واتساب/الويبهوك: أُزيلت من الشريط الجانبي وسُطِّحت ضمن «التكاملات
        # والقنوات» (نماذجها هناك تنشر لنقاط الحفظ القائمة). صفحاتها المستقلّة
        # تبقى متاحة عبر التكاملات حتى تكتمل هجرة واجهتها الغنية (Phase 1.1).
        c = _client(app)
        html = c.get("/admin/radius/integrations").get_data(as_text=True)
        assert "whatsapp_settings" in html or "واتساب" in html
        assert "wh_settings" in html or "Webhook" in html


# ════════════ (4) حذف المكرّر network_telegram_settings ════════════
class TestDuplicateRemoved:
    def test_dup_route_module_deleted(self):
        import os.path as _p
        here = _p.dirname(_p.dirname(__file__))
        assert not _p.exists(_p.join(here, "app", "radius", "routes",
                                     "network_telegram_settings.py"))

    def test_dup_register_not_imported(self):
        import app.radius.routes.blueprint as bp
        src = open(bp.__file__, encoding="utf-8").read()
        assert "register_network_telegram_routes" not in src

    def test_canonical_telegram_table_intact(self, app):
        # المخزن القانوني tenant_telegram_settings يبقى يعمل.
        with app.app_context():
            from app.radius.db.repos import tenant_telegram_settings_repo as repo
            repo.upsert(tenant_id=1, bot_token="123:abc", chat_id="-100", enabled=True)
            assert repo.is_configured(1) is True
