"""feat/telegram-admin-alerts — جرد تنبيهات الإدارة + إرسال تلجرام.

يغطّي: السجلّ/التصيير/المعاينة، التفعيل/التعطيل المُخزَّن، الإرسال المُزال
التكرار غير الحاجب، اختبار التنبيه + اختبار الاتصال، تشفير توكن البوت،
صفحة الإدارة (AJAX)، وتوصيل المُطلِق (إنشاء مشترك → تنبيه). شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "admin_alerts.db")
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


def _configure_bot(enabled=True):
    from app.radius.db.repos import tenant_telegram_settings_repo as repo
    repo.upsert(tenant_id=1, bot_token="123456:ABCDEF-test", chat_id="-100999",
                enabled=enabled, thread_id="")


def _capture_sends(monkeypatch):
    """يلتقط نصوص الرسائل المُرسَلة ويُعيد قائمة، ويجعل الإرسال متزامنًا."""
    sent = []
    from app.radius.services import telegram_notifier
    monkeypatch.setattr(telegram_notifier, "send_to_tenant",
                        lambda tid, text: (sent.append((tid, text)) or (True, "")))
    # اجعل dispatch متزامنًا (لا خيط) كي يكون الاختبار حتميًّا.
    from app.radius.services import admin_alerts

    class _SyncThread:
        def __init__(self, target=None, **kw): self._t = target
        def start(self): self._t() if self._t else None
    monkeypatch.setattr(admin_alerts.threading, "Thread", _SyncThread)
    return sent


# ════════════════════════════════════════════════════════════════════════
# (1) السجلّ + التصيير + المعاينة
# ════════════════════════════════════════════════════════════════════════
class TestCatalogueAndRender:

    def test_inventory_has_core_alerts(self):
        from app.radius.services import admin_alerts as aa
        keys = {a.key for a in aa.ALERTS}
        for required in ("subscriber_new", "loan_granted", "speed_boost",
                         "quota_exhausted", "subscriber_edited",
                         "mikrotik_connection_problem", "network_disconnect",
                         "loop_detected", "device_health"):
            assert required in keys

    def test_render_fills_fields_and_missing_safe(self):
        from app.radius.services import admin_alerts as aa
        out = aa.render("subscriber_new", {"username": "u1", "full_name": "Ali"})
        assert "u1" in out and "Ali" in out
        assert "—" in out  # حقل ناقص (plan/mobile) → «—» بلا انهيار

    def test_preview_uses_sample(self):
        from app.radius.services import admin_alerts as aa
        prev = aa.preview("speed_boost")
        assert "ahmad99" in prev and "🚀" in prev


# ════════════════════════════════════════════════════════════════════════
# (2) التفعيل/التعطيل + الإرسال + الاختبار
# ════════════════════════════════════════════════════════════════════════
class TestToggleAndDispatch:

    def test_toggle_persisted(self, app_ctx):
        from app.radius.services import admin_alerts as aa
        # quota_exhausted افتراضه معطّل
        assert aa.is_enabled(1, "quota_exhausted") is False
        aa.set_enabled(1, "quota_exhausted", True)
        assert aa.is_enabled(1, "quota_exhausted") is True
        # subscriber_new افتراضه مفعّل
        assert aa.is_enabled(1, "subscriber_new") is True
        aa.set_enabled(1, "subscriber_new", False)
        assert aa.is_enabled(1, "subscriber_new") is False

    def test_dispatch_sends_when_enabled_and_configured(self, app_ctx, monkeypatch):
        from app.radius.services import admin_alerts as aa
        sent = _capture_sends(monkeypatch)
        _configure_bot(enabled=True)
        aa.dispatch(1, "subscriber_new", {"username": "u1", "full_name": "Ali"})
        assert len(sent) == 1 and "u1" in sent[0][1]

    def test_dispatch_skips_when_disabled(self, app_ctx, monkeypatch):
        from app.radius.services import admin_alerts as aa
        sent = _capture_sends(monkeypatch)
        _configure_bot(enabled=True)
        aa.set_enabled(1, "subscriber_new", False)
        aa.dispatch(1, "subscriber_new", {"username": "u1"})
        assert sent == []

    def test_dispatch_skips_when_bot_not_configured(self, app_ctx, monkeypatch):
        from app.radius.services import admin_alerts as aa
        sent = _capture_sends(monkeypatch)
        # لا بوت مضبوط
        aa.dispatch(1, "subscriber_new", {"username": "u1"})
        assert sent == []

    def test_dispatch_dedup_within_window(self, app_ctx, monkeypatch):
        from app.radius.services import admin_alerts as aa
        sent = _capture_sends(monkeypatch)
        _configure_bot(enabled=True)
        aa.dispatch(1, "subscriber_new", {"username": "u1"}, dedup_key="u1")
        aa.dispatch(1, "subscriber_new", {"username": "u1"}, dedup_key="u1")
        assert len(sent) == 1  # الثانية مُزالة بالتكرار

    def test_send_test_returns_text_and_ok(self, app_ctx, monkeypatch):
        from app.radius.services import admin_alerts as aa
        sent = _capture_sends(monkeypatch)
        _configure_bot(enabled=True)
        res = aa.send_test(1, "loan_granted")
        assert res["ok"] is True and "سلفة" in res["text"]
        assert len(sent) == 1 and "اختبار" in sent[0][1]

    def test_send_test_when_not_configured(self, app_ctx):
        from app.radius.services import admin_alerts as aa
        res = aa.send_test(1, "loan_granted")
        assert res["ok"] is False and res["text"]  # نص المعاينة يبقى متاحًا

    def test_test_connection(self, app_ctx, monkeypatch):
        from app.radius.services import admin_alerts as aa
        sent = _capture_sends(monkeypatch)
        _configure_bot(enabled=True)
        assert aa.test_connection(1)["ok"] is True and len(sent) == 1


# ════════════════════════════════════════════════════════════════════════
# (3) تشفير توكن البوت
# ════════════════════════════════════════════════════════════════════════
class TestTokenEncryption:

    def test_token_stored_encrypted_and_decrypts(self, app_ctx):
        from app.radius.db.repos import tenant_telegram_settings_repo as repo
        from app.radius.db.connection import db
        repo.upsert(tenant_id=1, bot_token="SECRET-TOKEN-123", chat_id="c", enabled=True)
        raw = db().execute("SELECT bot_token FROM tenant_telegram_settings WHERE tenant_id=1").fetchone()["bot_token"]
        assert raw.startswith("enc:") and "SECRET-TOKEN-123" not in raw
        assert repo.get(1)["bot_token"] == "SECRET-TOKEN-123"  # فكّ التشفير

    def test_legacy_plaintext_still_readable(self, app_ctx):
        from app.radius.db.repos import tenant_telegram_settings_repo as repo
        from app.radius.db.connection import db, transaction
        from app.radius.db.helpers import now_iso
        with transaction() as conn:
            conn.execute("INSERT INTO tenant_telegram_settings(tenant_id, bot_token, chat_id, enabled, thread_id, updated_at) "
                         "VALUES (1,'plain-legacy','c',1,'',?)", (now_iso(),))
        assert repo.get(1)["bot_token"] == "plain-legacy"


# ════════════════════════════════════════════════════════════════════════
# (4) صفحة الإدارة (AJAX)
# ════════════════════════════════════════════════════════════════════════
class TestRoutePage:

    def _client(self, app_ctx):
        c = app_ctx.test_client()
        with c.session_transaction() as s:
            s["tenant_id"] = 1
            s["admin_id"] = 1
            s["is_super_admin"] = True
            s["_csrf_token"] = "tok"
        return c

    def _hdr(self):
        return {"X-CSRFToken": "tok", "X-Requested-With": "XMLHttpRequest"}

    def test_page_renders(self, app_ctx):
        html = self._client(app_ctx).get("/admin/radius/alerts/telegram").get_data(as_text=True)
        assert "إشعارات التلجرام" in html
        assert 'data-testid="alerts-table"' in html
        assert "إضافة مشترك جديد" in html  # عنصر من الجرد

    def test_toggle_via_ajax(self, app_ctx):
        from app.radius.services import admin_alerts as aa
        c = self._client(app_ctx)
        r = c.post("/admin/radius/alerts/telegram/toggle", headers=self._hdr(),
                   data={"key": "quota_exhausted", "enabled": "1"})
        assert r.status_code == 200 and r.get_json()["enabled"] is True
        assert aa.is_enabled(1, "quota_exhausted") is True

    def test_toggle_unknown_404(self, app_ctx):
        r = self._client(app_ctx).post("/admin/radius/alerts/telegram/toggle",
                                       headers=self._hdr(), data={"key": "bogus"})
        assert r.status_code == 404

    def test_test_alert_ajax(self, app_ctx, monkeypatch):
        sent = _capture_sends(monkeypatch)
        _configure_bot(enabled=True)
        r = self._client(app_ctx).post("/admin/radius/alerts/telegram/test",
                                       headers=self._hdr(), data={"key": "subscriber_new"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True and body["text"]

    def test_save_bot_keeps_token_when_blank(self, app_ctx):
        from app.radius.db.repos import tenant_telegram_settings_repo as repo
        c = self._client(app_ctx)
        c.post("/admin/radius/alerts/telegram/bot", data={
            "bot_token": "TOK-1", "chat_id": "-100", "enabled": "1", "_csrf_token": "tok"})
        assert repo.get(1)["bot_token"] == "TOK-1"
        # حفظ ثانٍ بتوكن فارغ → يبقى التوكن
        c.post("/admin/radius/alerts/telegram/bot", data={
            "bot_token": "", "chat_id": "-200", "enabled": "1", "_csrf_token": "tok"})
        cfg = repo.get(1)
        assert cfg["bot_token"] == "TOK-1" and cfg["chat_id"] == "-200"

    def test_test_connection_ajax(self, app_ctx, monkeypatch):
        _capture_sends(monkeypatch)
        _configure_bot(enabled=True)
        r = self._client(app_ctx).post("/admin/radius/alerts/telegram/test-connection", headers=self._hdr())
        assert r.status_code == 200 and r.get_json()["ok"] is True


# ════════════════════════════════════════════════════════════════════════
# (5) توصيل المُطلِق — إنشاء مشترك يُطلق التنبيه
# ════════════════════════════════════════════════════════════════════════
class TestTriggerWiring:

    def test_create_subscriber_dispatches_alert(self, app_ctx, monkeypatch):
        captured = []
        from app.radius.services import admin_alerts
        monkeypatch.setattr(admin_alerts, "dispatch",
                            lambda tid, key, ctx, **kw: captured.append((key, ctx)))
        from app.radius.core.types import Subscriber
        from app.radius.services.users import get_users_service
        get_users_service().create(actor="tester", sub=Subscriber(
            id=None, username="newsub", password="pw", tenant_id=1, full_name="New Sub"))
        assert any(k == "subscriber_new" and c["username"] == "newsub" for k, c in captured)
