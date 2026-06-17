"""feat/store-chat-attachment-idle-reminder — تذكير المحادثات غير المُجابة.

يغطّي store_chat_reminder_worker.poll_once:
  • رسالة زبون بلا ردّ أقدم من العتبة → تنبيه store_chat_unanswered.
  • ردّ المدير (رسالة admin) قبل العتبة → لا تذكير.
  • ضبط حالة «مُعالَجة» (set_status resolved) قبل العتبة → لا تذكير.
  • تذكير واحد لكل خيط (لا تكرار كل دقّة) — حتى رسالة زبون أحدث.
  • العتبة إعداد قابل للضبط + الرابط العميق للردّ حاضر.
شغّل الملف وحده.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "store_chat_reminder.db")
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


def _capture(monkeypatch):
    hits = []
    from app.radius.services import admin_alerts
    monkeypatch.setattr(admin_alerts, "dispatch",
                        lambda tid, key, ctx=None, **kw: (
                            hits.append((ctx or {}, kw)) if key == "store_chat_unanswered" else None))
    return hits


def _svc():
    from app.radius.services.store_chat import StoreChatService
    return StoreChatService(tenant_id=1)


def _customer(svc, cu=42, body="بحاجة مساعدة"):
    svc.post_message(card_user_id=cu, sender="customer", body=body)


def _admin(svc, cu=42, body="أهلاً"):
    svc.post_message(card_user_id=cu, sender="admin", body=body, admin_actor="المدير")


def _backdate_last(cu, minutes):
    _set_created_at(cu, datetime.utcnow() - timedelta(minutes=minutes))


def _set_created_at(cu, dt):
    """يضبط زمن إنشاء آخر رسالة لهذا الزبون إلى لحظة محدّدة (للتحكّم الزمني)."""
    from app.radius.db.connection import transaction
    ts = dt.isoformat() + "Z"
    with transaction() as conn:
        conn.execute(
            "UPDATE store_chat_messages SET created_at=? WHERE id=("
            "SELECT MAX(id) FROM store_chat_messages WHERE tenant_id=1 AND card_user_id=?)",
            (ts, int(cu)))


def _poll_at(when):
    from app.workers.store_chat_reminder_worker import poll_once
    return poll_once(now=when)


def _poll():
    from app.workers.store_chat_reminder_worker import poll_once
    return poll_once(now=datetime.utcnow())


# ════════════════════════════════════════════════════════════════════════
# يُطلِق / لا يُطلِق
# ════════════════════════════════════════════════════════════════════════
class TestReminderFires:

    def test_unanswered_past_threshold_fires(self, app_ctx, monkeypatch):
        hits = _capture(monkeypatch)
        _customer(_svc())
        _backdate_last(42, 90)            # 90د > 60 (الافتراضي)
        _poll()
        assert len(hits) == 1
        assert hits[0][0].get("card_user_id") == 42

    def test_recent_unanswered_not_fired(self, app_ctx, monkeypatch):
        hits = _capture(monkeypatch)
        _customer(_svc())
        _backdate_last(42, 20)            # 20د < 60 → لا تذكير بعد
        _poll()
        assert hits == []


# ════════════════════════════════════════════════════════════════════════
# «مُجاب» = ردّ أو حالة
# ════════════════════════════════════════════════════════════════════════
class TestAnsweredSuppresses:

    def test_admin_reply_suppresses(self, app_ctx, monkeypatch):
        hits = _capture(monkeypatch)
        svc = _svc()
        _customer(svc)
        _admin(svc)                       # ردّ المدير → آخر رسالة admin
        _backdate_last(42, 120)           # حتى لو قديمة، الخيط مُجاب
        _poll()
        assert hits == []

    def test_status_resolved_suppresses(self, app_ctx, monkeypatch):
        hits = _capture(monkeypatch)
        svc = _svc()
        _customer(svc)
        _backdate_last(42, 120)
        svc.set_status(card_user_id=42, status="resolved", actor="المدير")
        _poll()
        assert hits == []                 # عولجت بضبط الحالة دون ردّ


# ════════════════════════════════════════════════════════════════════════
# مرّة لكل خيط + العتبة
# ════════════════════════════════════════════════════════════════════════
class TestOncePerThreadAndThreshold:

    def test_fires_once_not_every_tick(self, app_ctx, monkeypatch):
        hits = _capture(monkeypatch)
        _customer(_svc())
        _backdate_last(42, 90)
        _poll(); _poll(); _poll()         # ثلاث دورات
        assert len(hits) == 1             # تذكير واحد فقط

    def test_new_customer_message_re_reminds(self, app_ctx, monkeypatch):
        # توقيت صريح: الرسالة الثانية تأتي **بعد** أوّل تذكير، فيُعاد التذكير.
        hits = _capture(monkeypatch)
        svc = _svc()
        base = datetime(2026, 6, 1, 10, 0, 0)
        _customer(svc, body="١")
        _set_created_at(42, base)
        _poll_at(base + timedelta(minutes=90))    # تذكير 1 (reminded_at = base+90)
        _customer(svc, body="٢")                  # رسالة زبون أحدث من التذكير
        _set_created_at(42, base + timedelta(minutes=100))
        _poll_at(base + timedelta(minutes=200))   # تذكير 2 (msg2 أحدث من التذكير)
        assert len(hits) == 2

    def test_threshold_setting_respected(self, app_ctx, monkeypatch):
        from app.radius.db.repos import tenants_repo
        hits = _capture(monkeypatch)
        tenants_repo.set_setting(1, "alerts.store_chat.unanswered_reminder_minutes", "30")
        _customer(_svc())
        _backdate_last(42, 45)            # 45د ≥ 30 → يُطلِق
        _poll()
        assert len(hits) == 1


# ════════════════════════════════════════════════════════════════════════
# المواصفة + الرابط العميق
# ════════════════════════════════════════════════════════════════════════
class TestSpecAndLink:

    def test_spec_present_default_on_and_action(self, app_ctx):
        from app.radius.services import admin_alerts as aa
        from app.radius.services.alert_links import ACTION_ALERTS
        by = {a.key: a for a in aa.ALERTS}
        assert "store_chat_unanswered" in by
        assert by["store_chat_unanswered"].default_enabled is True
        assert "store_chat_unanswered" in ACTION_ALERTS

    def test_render_has_reply_deeplink(self, app_ctx):
        ctx = app_ctx.test_request_context("/", base_url="https://panel.example.com")
        ctx.push()
        try:
            from flask import g
            g.tenant_id = 1
            from app.radius.services import admin_alerts as aa
            out = aa.render("store_chat_unanswered",
                            {"name": "سالم", "since": "ساعة", "card_user_id": 42})
            assert "🔗 للتدخّل:" in out
            assert "/admin/radius/store-support?chat=42#chat" in out
        finally:
            ctx.pop()
