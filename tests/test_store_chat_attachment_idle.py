"""feat/store-chat-attachment-idle-reminder — توسعة دور «بانتظار ردّ».

يغطّي امتدادي قاعدة notify_chat (بالإضافة لقاعدة الدمج السابقة):
  • رسالة بمرفق/صورة تُطلِق التنبيه حتى لو كان الخيط منتظِرًا.
  • رسالة بعد فجوة خمول (≥ alerts.store_chat.idle_gap_minutes، افتراضي 10)
    تُطلِق حتى لو منتظِرًا — والفجوة محسوبة عبر الساعات/الأيام (٨س/اليوم التالي).
  • النصّ المتتابع السريع يبقى مكتومًا. العتبة إعداد قابل للضبط، افتراضي 10.
شغّل الملف وحده.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "store_chat_idle.db")
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
    hits = []
    from app.radius.services import admin_alerts
    monkeypatch.setattr(admin_alerts, "dispatch",
                        lambda tid, key, ctx=None, **kw: (
                            hits.append((ctx or {}, kw)) if key == "store_chat" else None))
    return hits


def _svc():
    from app.radius.services.store_chat import StoreChatService
    return StoreChatService(tenant_id=1)


def _customer(svc, cu=42, body="مرحبا", image_path=""):
    svc.post_message(card_user_id=cu, sender="customer", body=body,
                     image_path=image_path)


def _backdate_last(cu, minutes):
    """يُرجِع زمن إنشاء آخر رسالة لهذا الزبون إلى الوراء (لمحاكاة فجوة)."""
    from app.radius.db.connection import transaction
    ts = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat() + "Z"
    with transaction() as conn:
        conn.execute(
            "UPDATE store_chat_messages SET created_at=? WHERE id=("
            "SELECT MAX(id) FROM store_chat_messages WHERE tenant_id=1 AND card_user_id=?)",
            (ts, int(cu)))


def _set_gap(minutes):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "alerts.store_chat.idle_gap_minutes", str(minutes))


# ════════════════════════════════════════════════════════════════════════
# المرفقات
# ════════════════════════════════════════════════════════════════════════
class TestAttachment:

    def test_attachment_fires_while_pending(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, body="رسالة ١")             # يُطلِق (أوّل)
        _customer(svc, body="", image_path="chat/receipt.png")  # مرفق → يُطلِق رغم الانتظار
        assert len(hits) == 2

    def test_plain_consecutive_text_still_suppressed(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, body="رسالة ١")   # يُطلِق
        _customer(svc, body="رسالة ٢")   # نصّ متتابع سريع → مكتوم
        assert len(hits) == 1


# ════════════════════════════════════════════════════════════════════════
# فجوة الخمول
# ════════════════════════════════════════════════════════════════════════
class TestIdleGap:

    def test_idle_gap_fires_while_pending_default_10(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, body="رسالة ١")   # يُطلِق
        _backdate_last(42, 15)            # الرسالة السابقة صارت قبل 15 دقيقة
        _customer(svc, body="رسالة ٢")   # فجوة 15د ≥ 10 (الافتراضي) → يُطلِق
        assert len(hits) == 2

    def test_eight_hour_gap_fires(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, body="صباحًا")
        _backdate_last(42, 8 * 60)        # ٨ ساعات (عبر الساعات)
        _customer(svc, body="مساءً")
        assert len(hits) == 2

    def test_next_day_gap_fires(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, body="أمس")
        _backdate_last(42, 26 * 60)       # اليوم التالي (عبر الأيام)
        _customer(svc, body="اليوم")
        assert len(hits) == 2

    def test_small_gap_suppressed(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        svc = _svc()
        _customer(svc, body="رسالة ١")
        _backdate_last(42, 3)             # 3 دقائق < 10 → مكتوم
        _customer(svc, body="رسالة ٢")
        assert len(hits) == 1

    def test_threshold_setting_respected(self, app_ctx, monkeypatch):
        hits = _capture_chat(monkeypatch)
        _set_gap(30)                      # عتبة 30 دقيقة
        svc = _svc()
        _customer(svc, body="رسالة ١")
        _backdate_last(42, 20)            # 20د < 30 → مكتوم
        _customer(svc, body="رسالة ٢")
        assert len(hits) == 1
        _backdate_last(42, 40)            # 40د ≥ 30 → يُطلِق
        _customer(svc, body="رسالة ٣")
        assert len(hits) == 2

    def test_default_is_ten(self, app_ctx):
        from app.radius.services.store_alerts import (_DEFAULT_IDLE_GAP_MIN,
                                                      _idle_gap_minutes)
        assert _DEFAULT_IDLE_GAP_MIN == 10
        assert _idle_gap_minutes(1) == 10   # بلا إعداد → الافتراضي
