# -*- coding: utf-8 -*-
"""تسليم إشعارات المشترك — end-to-end.

يثبّت: كل حدث مُفعَّل يُسلَّم لتيليجرام المشترك المربوط؛ الحدث/القناة المُعطَّلة
لا تُرسل؛ القناة غير المهيّأة تُتخطّى بنظافة؛ القوالب تحوي الحقول الصحيحة؛
صفحة الإعداد تحفظ القنوات وتتحكّم بالتسليم؛ send_to_chat القانوني. شغّل الملف
وحده."""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "subnotif.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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


def _bot(enabled=True):
    from app.radius.db.repos import tenant_telegram_settings_repo as tg
    tg.upsert(tenant_id=1, bot_token="123:abc", chat_id="-100", enabled=enabled)


def _subscriber(app, *, username="shop1", chat_id="555111", mobile="0599",
                plan="10 ميجا", expire="2026-07-01"):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            pid = None
            if plan:
                c.execute("INSERT INTO access_plans(tenant_id,name,price,created_at) "
                          "VALUES(1,?,20,'2026-01-01')", (plan,))
                pid = c.execute("SELECT id FROM access_plans ORDER BY id DESC LIMIT 1").fetchone()[0]
            c.execute("INSERT INTO subscribers(tenant_id,username,password,full_name,"
                      "mobile,telegram_chat_id,status,plan_id,expire_at,created_at) "
                      "VALUES(1,?,'p','أحمد',?,?,'enabled',?,?,'2026-01-01')",
                      (username, mobile, chat_id, pid, expire))
            return c.execute("SELECT id FROM subscribers WHERE username=?", (username,)).fetchone()[0]


def _capture_tg(monkeypatch):
    sent = []
    from app.radius.services import telegram_notifier
    monkeypatch.setattr(telegram_notifier, "send_to_chat",
                        lambda tid, chat, text: (sent.append((tid, chat, text)) or (True, "")))
    return sent


def _capture_http(monkeypatch):
    calls = []
    from app.radius.services import comms_providers

    class _R:
        ok = True
    monkeypatch.setattr(comms_providers, "http_send", lambda **k: (calls.append(k) or _R()))
    return calls


# ════════════ (1) حدث مُفعَّل → تيليجرام المشترك ════════════
def test_enabled_event_delivers_to_subscriber_telegram(app, monkeypatch):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        _bot()
        _subscriber(app)
        sent = _capture_tg(monkeypatch)
        sn.set_channels(1, "expiry_soon", ["telegram"])
        r = sn.deliver(1, "expiry_soon", username="shop1", context={"days": 3})
        assert r["sent"].get("telegram") is True
        assert len(sent) == 1
        assert sent[0][1] == "555111"   # الإرسال إلى chat_id المشترك


def test_message_template_contains_event_fields(app, monkeypatch):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        _bot()
        _subscriber(app)
        sent = _capture_tg(monkeypatch)
        sn.set_channels(1, "expiry_soon", ["telegram"])
        sn.deliver(1, "expiry_soon", username="shop1", context={"days": 5})
        text = sent[0][2]
        assert "5" in text and "أحمد" in text and "10 ميجا" in text and "2026-07-01" in text


def test_payment_template_has_amount(app, monkeypatch):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        _bot()
        _subscriber(app)
        sent = _capture_tg(monkeypatch)
        sn.set_channels(1, "payment_received", ["telegram"])
        sn.deliver(1, "payment_received", username="shop1",
                   context={"amount": 20, "currency": "₪"})
        assert "20" in sent[0][2] and "₪" in sent[0][2]


# ════════════ (2) حدث/قناة مُعطَّلة → لا إرسال ════════════
def test_disabled_event_does_not_send(app, monkeypatch):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        _bot()
        _subscriber(app)
        sent = _capture_tg(monkeypatch)
        sn.set_channels(1, "plan_changed", [])      # explicitly disabled
        r = sn.deliver(1, "plan_changed", username="shop1", context={"plan": "X"})
        assert r["sent"] == {} and "event_disabled" in r["skipped"]
        assert len(sent) == 0


def test_channel_off_does_not_send_that_channel(app, monkeypatch):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        _bot()
        _subscriber(app)
        sent = _capture_tg(monkeypatch)
        http = _capture_http(monkeypatch)
        from app.radius.services import comms_providers
        comms_providers.save_channel_config(1, "sms", {
            "enabled": "1", "send_url_template": "https://s/x?to={phone}&t={msg}",
            "http_method": "GET"})
        sn.set_channels(1, "disabled", ["sms"])     # SMS only, no telegram
        sn.deliver(1, "disabled", username="shop1")
        assert len(sent) == 0                        # telegram NOT attempted
        assert len(http) == 1                        # only SMS sent


# ════════════ (3) قناة غير مهيّأة → تخطٍّ نظيف ════════════
def test_unconfigured_whatsapp_skips_cleanly(app, monkeypatch):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        _bot()
        _subscriber(app)
        _capture_tg(monkeypatch)
        http = _capture_http(monkeypatch)
        sn.set_channels(1, "disabled", ["telegram", "whatsapp"])  # WA not configured
        r = sn.deliver(1, "disabled", username="shop1")
        assert "whatsapp_not_configured" in r["skipped"]
        assert len(http) == 0                        # no HTTP send attempted
        assert r["sent"].get("telegram") is True     # telegram still delivered


def test_telegram_not_connected_skips(app, monkeypatch):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        _bot()
        _subscriber(app, chat_id="")                 # subscriber didn't connect TG
        # No mock: real send_to_chat short-circuits on empty chat_id (no network).
        sn.set_channels(1, "expiry_soon", ["telegram"])
        r = sn.deliver(1, "expiry_soon", username="shop1", context={"days": 1})
        assert "telegram_not_connected" in r["skipped"]
        assert r["sent"].get("telegram") is not True


def test_no_subscriber_skips(app):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        r = sn.deliver(1, "expiry_soon", username="ghost")
        assert "no_subscriber" in r["skipped"] and r["sent"] == {}


# ════════════ (4) القنوات تُحفظ وتتحكّم بالتسليم ════════════
def test_channels_persist_roundtrip(app):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        sn.set_channels(1, "payment_received", ["telegram", "sms"])
        assert sn.channels_for(1, "payment_received") == {"telegram", "sms"}
        sn.set_channels(1, "payment_received", [])
        assert sn.channels_for(1, "payment_received") == set()   # disabled sticks
        assert sn.is_enabled(1, "payment_received") is False


def test_default_channels_when_unset(app):
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        # غير مضبوط → القنوات الافتراضية للحدث (telegram).
        assert sn.channels_for(1, "expiry_soon") == {"telegram"}


# ════════════ (5) قناة تيليجرام القانونيّة send_to_chat ════════════
def test_send_to_chat_skips_without_token(app):
    with app.app_context():
        from app.radius.services import telegram_notifier as tn
        # bot غير مهيّأ → تخطٍّ صامت (False,'').
        ok, err = tn.send_to_chat(1, "555", "hi")
        assert ok is False and err == ""


def test_send_to_chat_skips_empty_chat(app):
    with app.app_context():
        from app.radius.services import telegram_notifier as tn
        _bot()
        ok, err = tn.send_to_chat(1, "", "hi")
        assert ok is False and err == ""


# ════════════ (6) الصفحة + نقطة الحفظ ════════════
def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def test_page_renders_with_toggles(app):
    c = _client(app)
    html = c.get("/admin/radius/subscriber-notifications").get_data(as_text=True)
    assert html and "إشعارات المشتركين" in html
    assert "data-sn-chan" in html              # per-channel chips
    assert "مُوصَّل" in html                    # live badge for wired events
    assert "قرب انتهاء الاشتراك" in html


def test_set_channels_endpoint_persists_and_gates(app):
    c = _client(app)
    c.get("/admin/radius/subscriber-notifications")   # mint CSRF
    with c.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = c.post("/admin/radius/subscriber-notifications/channels",
               data={"key": "expiry_soon", "channels": ["telegram", "sms"],
                     "_csrf_token": tok or ""}, headers={"X-CSRFToken": tok or ""})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    with app.app_context():
        from app.radius.services import subscriber_notify as sn
        assert sn.channels_for(1, "expiry_soon") == {"telegram", "sms"}


def test_set_channels_unknown_event_404(app):
    c = _client(app)
    c.get("/admin/radius/subscriber-notifications")
    with c.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = c.post("/admin/radius/subscriber-notifications/channels",
               data={"key": "nope", "_csrf_token": tok or ""},
               headers={"X-CSRFToken": tok or ""})
    assert r.status_code == 404


# ════════════ (7) ربط مواقع الأحداث ════════════
def test_users_disable_enable_fire_subscriber_notify(app, monkeypatch):
    """disable()/enable() تستدعي subscriber_notify.dispatch بالحدث الصحيح."""
    calls = []
    from app.radius.services import subscriber_notify as sn
    monkeypatch.setattr(sn, "dispatch",
                        lambda tid, key, **kw: calls.append((key, kw.get("username"))))
    with app.app_context():
        _subscriber(app, username="ux")
        from app.radius.services.users import get_users_service
        svc = get_users_service()
        svc.disable(actor="admin", username="ux")
        svc.enable(actor="admin", username="ux")
    keys = [k for k, _u in calls]
    assert "disabled" in keys and "enabled" in keys
