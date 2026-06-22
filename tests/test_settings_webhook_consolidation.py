"""chore/customer-panel-cleanup-2 — توحيد الويبهوك + تخفيف إعدادات النظام.

يغطّي:
  • مُرسِل الأحداث يعتمد اشتراكات webhook_subscriptions لا المفتاح المفرد
    المُزال webhook.target_url (ضبط المفتاح المفرد وحده لا يُرسِل شيئًا؛
    وجود اشتراك يُرسِل).
  • migration 134 يُرحّل قيمة webhook.target_url/secret المفردة المضبوطة
    سابقًا إلى اشتراك (دون فقدان بيانات) — وهو idempotent.
  • صفحة الإعدادات تُصيَّر 200 ولم تعد تعرض الحقول المُزالة/المخفية.
  • الإعدادات المخفية ما تزال تُحلّ إلى افتراضاتها في الكود (get_setting).

شغّل الملف وحده (عزل اختبارات لكل ملف).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "whcons.db")
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


def _with_tenant(tid: int = 1):
    from flask import g
    g.tenant_id = tid


def _client(app_ctx):
    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["tenant_id"] = 1
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["_csrf_token"] = "tok"
    return c


# ════════════════════════════════════════════════════════════════════════
# (1) مُرسِل الأحداث يعتمد الاشتراكات لا المفتاح المفرد المُزال
# ════════════════════════════════════════════════════════════════════════
class TestDispatchUsesSubscriptions:

    def test_single_setting_alone_dispatches_nothing(self, app_ctx):
        """ضبط webhook.target_url المفرد فقط (بلا اشتراك) لا يُنتج أي إرسال —
        إثبات أنّ المُرسِل لا يقرأ المفتاح المفرد المُزال."""
        from app.radius.db.repos import tenants_repo, webhooks_repo
        from app.webhooks.dispatcher import dispatch_event
        _with_tenant(1)
        tenants_repo.set_setting(1, "webhook.target_url", "https://old.example.com/hook")
        tenants_repo.set_setting(1, "webhook.secret", "old-secret")

        dispatch_event("webhook.test", {"hello": "world"}, tenant_id=1)

        assert webhooks_repo.list_deliveries(1) == []  # لا شيء أُرسِل

    def test_subscription_dispatches(self, app_ctx):
        """وجود اشتراك نشط → الحدث يُوضَع في طابور الإرسال."""
        from app.radius.core.types_saas import WebhookSubscription
        from app.radius.db.repos import webhooks_repo
        from app.webhooks.dispatcher import dispatch_event
        _with_tenant(1)
        webhooks_repo.upsert_sub(WebhookSubscription(
            id=None, tenant_id=1, target_url="https://new.example.com/hook",
            secret="hmac-secret", enabled=True,
        ))

        dispatch_event("webhook.test", {"hello": "world"}, tenant_id=1)

        deliveries = webhooks_repo.list_deliveries(1)
        assert len(deliveries) == 1
        assert deliveries[0].event == "webhook.test"


# ════════════════════════════════════════════════════════════════════════
# (2) migration 134 يُرحّل القيمة المفردة المضبوطة سابقًا إلى اشتراك
# ════════════════════════════════════════════════════════════════════════
class TestMigrationBackfill:

    _MIG = Path("app/radius/db/migrations/134_webhook_settings_to_subscription.sql")

    def test_existing_single_value_migrates_to_subscription(self, app_ctx):
        from app.radius.db.connection import db
        from app.radius.db.repos import tenants_repo, webhooks_repo
        _with_tenant(1)
        # مستأجر ضبط الرابط المفرد قبل التوحيد ولا يملك اشتراكًا.
        tenants_repo.set_setting(1, "webhook.target_url", "https://legacy.example.com/wh")
        tenants_repo.set_setting(1, "webhook.secret", "legacy-hmac")
        assert webhooks_repo.list_subs(1) == []

        db().executescript(self._MIG.read_text(encoding="utf-8"))

        subs = webhooks_repo.list_subs(1)
        assert len(subs) == 1
        assert subs[0].target_url == "https://legacy.example.com/wh"
        assert subs[0].secret == "legacy-hmac"
        assert subs[0].enabled is True

    def test_migration_is_idempotent(self, app_ctx):
        """إعادة تنفيذ الـ migration لا تُنشئ اشتراكًا مكرّرًا."""
        from app.radius.db.connection import db
        from app.radius.db.repos import tenants_repo, webhooks_repo
        _with_tenant(1)
        tenants_repo.set_setting(1, "webhook.target_url", "https://legacy.example.com/wh")
        db().executescript(self._MIG.read_text(encoding="utf-8"))
        db().executescript(self._MIG.read_text(encoding="utf-8"))
        assert len(webhooks_repo.list_subs(1)) == 1

    def test_no_setting_no_subscription_created(self, app_ctx):
        """بلا قيمة مفردة مضبوطة — لا يُنشأ اشتراك فارغ."""
        from app.radius.db.connection import db
        from app.radius.db.repos import webhooks_repo
        _with_tenant(1)
        db().executescript(self._MIG.read_text(encoding="utf-8"))
        assert webhooks_repo.list_subs(1) == []

    def test_existing_subscription_not_clobbered(self, app_ctx):
        """مستأجر يملك اشتراكًا أصلًا — لا يُضاف اشتراك من القيمة المفردة."""
        from app.radius.core.types_saas import WebhookSubscription
        from app.radius.db.connection import db
        from app.radius.db.repos import tenants_repo, webhooks_repo
        _with_tenant(1)
        webhooks_repo.upsert_sub(WebhookSubscription(
            id=None, tenant_id=1, target_url="https://kept.example.com/wh",
            secret="kept", enabled=True,
        ))
        tenants_repo.set_setting(1, "webhook.target_url", "https://legacy.example.com/wh")
        db().executescript(self._MIG.read_text(encoding="utf-8"))
        subs = webhooks_repo.list_subs(1)
        assert len(subs) == 1
        assert subs[0].target_url == "https://kept.example.com/wh"


# ════════════════════════════════════════════════════════════════════════
# (3) صفحة الإعدادات تُصيَّر 200 ولم تعد تعرض الحقول المُزالة/المخفية
# ════════════════════════════════════════════════════════════════════════
class TestSettingsPageLeaner:

    _REMOVED = [
        "webhook.target_url", "webhook.secret",          # وُحِّدت في /webhooks
        "session.timeout_minutes", "api.rate_limit_per_minute",
        "display.records_per_page", "mikrotik.default_router_id",  # افتراضات تقنية
    ]
    _KEPT = ["billing.currency", "system.name", "branding.primary_color",
             "network.radius_server_ip"]

    def test_page_renders_200(self, app_ctx):
        res = _client(app_ctx).get("/admin/radius/settings")
        assert res.status_code == 200

    def test_removed_fields_absent(self, app_ctx):
        html = _client(app_ctx).get("/admin/radius/settings").get_data(as_text=True)
        for key in self._REMOVED:
            assert ('name="%s"' % key) not in html, f"حقل مُزال ما يزال ظاهرًا: {key}"

    def test_kept_fields_present(self, app_ctx):
        html = _client(app_ctx).get("/admin/radius/settings").get_data(as_text=True)
        for key in self._KEPT:
            assert ('name="%s"' % key) in html, f"حقل مطلوب اختفى: {key}"

    def test_notify_webhook_tab_gone(self, app_ctx):
        html = _client(app_ctx).get("/admin/radius/settings").get_data(as_text=True)
        assert 'data-st-tab="notify"' not in html
        assert "إشعارات Webhook الخارجية" not in html


# ════════════════════════════════════════════════════════════════════════
# (4) الإعدادات المخفية ما تزال تُحلّ إلى افتراضاتها في الكود
# ════════════════════════════════════════════════════════════════════════
class TestHiddenDefaultsStillResolve:

    _DEFAULTS = {
        "session.timeout_minutes": "60",
        "api.rate_limit_per_minute": "60",
        "display.records_per_page": "20",
        "mikrotik.default_router_id": "",
    }

    def test_defaults_resolve_when_unset(self, app_ctx):
        from app.radius.db.repos import tenants_repo
        _with_tenant(1)
        for key, default in self._DEFAULTS.items():
            assert tenants_repo.get_setting(1, key, default) == default
