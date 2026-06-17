"""feat/store-chat-pending-transition — تنبيه تلجرام مرّة لكل دور «بانتظار ردّ».

القاعدة: store_chat يُطلَق مرّة عند فتح دور انتظار جديد (أوّل رسالة، أو عودة
الزبون بعد ردّ الموظّف) — لا لكل رسالة متتابعة في خيط منتظِر أصلًا. الإشارة
من اتجاه الرسالة السابقة في store_chat_messages (customer مقابل admin).

يغطّي: أوّل رسالة → تنبيه واحد؛ رسالة زبون متتالية → بلا تنبيه؛ رسالة زبون
بعد ردّ المدير → تنبيه جديد؛ بقاء card_user_id (رابط الردّ) في السياق؛
وتغيّر الافتراضي إلى ON. شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "store_chat_pending.db")
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


def _capture_chat(monkeypatch):
    """يلتقط نداءات dispatch لـstore_chat فقط."""
    hits = []
    from app.radius.services import admin_alerts
    real = admin_alerts.dispatch

    def fake(tid, key, ctx=None, **kw):
        if key == "store_chat":
            hits.append((ctx or {}, kw))
    monkeypatch.setattr(admin_alerts, "dispatch", fake)
    return hits


def _svc():
    from app.radius.services.store_chat import StoreChatService
    return StoreChatService(tenant_id=1)


def _customer(svc, cu=42, body="مرحبا"):
    svc.post_message(card_user_id=cu, sender="customer", body=body)


def _admin(svc, cu=42, body="أهلاً، كيف أساعدك؟"):
    svc.post_message(card_user_id=cu, sender="admin", body=body, admin_actor="المدير")


# ════════════════════════════════════════════════════════════════════════
# قاعدة الدور «بانتظار ردّ»
# ════════════════════════════════════════════════════════════════════════
class TestPendingTransition:

    def test_first_inbound_fires_once(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        _customer(_svc())
        assert len(hits) == 1
        assert hits[0][0].get("card_user_id") == 42   # رابط الردّ موجود

    def test_consecutive_inbound_does_not_fire(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, body="رسالة ١")   # يُطلِق
        _customer(svc, body="رسالة ٢")   # الخيط منتظِر أصلًا → لا يُطلِق
        _customer(svc, body="رسالة ٣")   # كذلك
        assert len(hits) == 1

    def test_inbound_after_staff_reply_fires_again(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, body="سؤال أول")   # يُطلِق (1)
        _admin(svc)                        # ردّ الموظّف → لا تنبيه
        _customer(svc, body="سؤال ثانٍ")  # عاد الزبون بعد الردّ → يُطلِق (2)
        assert len(hits) == 2
        assert all(c.get("card_user_id") == 42 for c, _ in hits)

    def test_full_sequence_one_ping_per_turn(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc)        # 1: فتح
        _customer(svc)        # منتظِر → لا
        _admin(svc)           # ردّ → لا
        _customer(svc)        # 2: عاد الزبون
        _customer(svc)        # منتظِر → لا
        _admin(svc)           # ردّ → لا
        _customer(svc)        # 3: عاد الزبون
        assert len(hits) == 3

    def test_separate_users_independent(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, cu=1)
        _customer(svc, cu=2)
        _customer(svc, cu=1)   # خيط 1 منتظِر → لا
        assert len(hits) == 2
        assert {c.get("card_user_id") for c, _ in hits} == {1, 2}

    def test_dedup_key_unique_per_turn(self, app_ctx, monkeypatch):
        """مفتاح إزالة التكرار يحمل معرّف الرسالة فلا تُحجب الأدوار المشروعة."""
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc)
        _admin(svc)
        _customer(svc)
        keys = [kw.get("dedup_key") for _, kw in hits]
        assert len(keys) == 2 and keys[0] != keys[1]
        assert all(str(k).startswith("store_chat:42:") for k in keys)


# ════════════════════════════════════════════════════════════════════════
# الافتراضي ON + الرابط
# ════════════════════════════════════════════════════════════════════════
class TestDefaultOnAndLink:

    def test_store_chat_default_on(self, app_ctx):
        from app.radius.services import admin_alerts as aa
        by = {a.key: a for a in aa.ALERTS}
        assert by["store_chat"].default_enabled is True
        assert aa.is_enabled(1, "store_chat") is True

    def test_render_keeps_reply_deeplink(self, app_ctx):
        ctx = app_ctx.test_request_context("/", base_url="https://panel.example.com")
        ctx.push()
        try:
            from flask import g
            g.tenant_id = 1
            from app.radius.services import admin_alerts as aa
            out = aa.render("store_chat", {"name": "سالم", "card_user_id": 42})
            assert "🔗 للتدخّل:" in out
            assert "/admin/radius/store-support?chat=42#chat" in out
        finally:
            ctx.pop()
