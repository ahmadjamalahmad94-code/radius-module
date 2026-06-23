# -*- coding: utf-8 -*-
"""تسليم إشعارات المشترك (مُوحَّد على notifications_engine).

بعد التوحيد: لا سطح موازٍ — صفحة «إشعارات المشتركين» تُحرّك notifications_engine،
و communications/notifications يُعاد توجيهه. التسليم يصل المشترك نفسه: تيليجرام
إلى subscribers.telegram_chat_id (أحداث المشترك)، وأحداث الشبكة → محادثة المشغّل.
شغّل الملف وحده."""
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


def _subscriber(app, *, username="shop1", chat_id="777999", mobile="0599"):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO subscribers(tenant_id,username,password,full_name,"
                      "mobile,telegram_chat_id,status,created_at) "
                      "VALUES(1,?,'p','أحمد',?,?,'enabled','2026-01-01')",
                      (username, mobile, chat_id))


def _capture(monkeypatch):
    chat, tenant = [], []
    from app.radius.services import telegram_notifier as tn
    monkeypatch.setattr(tn, "send_to_chat",
                        lambda tid, cid, text: (chat.append((cid, text)) or (True, "")))
    monkeypatch.setattr(tn, "send_to_tenant",
                        lambda tid, text: (tenant.append(text) or (True, "")))
    return chat, tenant


def _sub(app, username="shop1"):
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        return ne.find_subscriber(1, username=username)


# ════════════ (1) تسليم تيليجرام للمشترك ════════════
def test_subscriber_event_telegram_to_subscriber_chat(app, monkeypatch):
    _bot(); _subscriber(app)
    chat, tenant = _capture(monkeypatch)
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        ne.save_rules(1, {"near_expiry__enabled": "1", "near_expiry__channels": ["telegram"]},
                      only_keys=["near_expiry"])
        r = ne.notify_event("near_expiry", tenant_id=1, subscriber=_sub(app),
                            context={"days": 3})
    assert r.fired and r.sent.get("telegram") is True
    assert len(chat) == 1 and chat[0][0] == "777999"   # subscriber's own chat
    assert len(tenant) == 0                              # NOT the operator chat
    assert "3" in chat[0][1]                             # {days} rendered


def test_network_event_telegram_to_tenant_chat(app, monkeypatch):
    _bot()
    chat, tenant = _capture(monkeypatch)
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        r = ne.notify_event("router_down", tenant_id=1, subscriber=None,
                            context={"device": "CCR", "ip": "10.0.0.1", "time": "now"})
    assert r.sent.get("telegram") is True
    assert len(tenant) == 1 and len(chat) == 0          # operator chat, not subscriber


def test_no_chat_id_telegram_skips(app, monkeypatch):
    _bot(); _subscriber(app, chat_id="")               # subscriber not connected
    chat, tenant = _capture(monkeypatch)
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        ne.save_rules(1, {"near_expiry__enabled": "1", "near_expiry__channels": ["telegram"]},
                      only_keys=["near_expiry"])
        r = ne.notify_event("near_expiry", tenant_id=1, subscriber=_sub(app), context={"days": 1})
    assert r.sent.get("telegram") is False and len(chat) == 0
    assert "telegram" in r.errors                        # clear skip reason


# ════════════ (2) البوّابة (تفعيل/قناة) ════════════
def test_disabled_event_no_send(app, monkeypatch):
    _bot(); _subscriber(app)
    chat, tenant = _capture(monkeypatch)
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        ne.save_rules(1, {"plan_changed__enabled": "0", "plan_changed__channels": ["telegram"]},
                      only_keys=["plan_changed"])
        r = ne.notify_event("plan_changed", tenant_id=1, subscriber=_sub(app))
    assert r.fired is False and r.reason == "disabled" and len(chat) == 0


def test_unconfigured_sms_whatsapp_skip_clean(app, monkeypatch):
    _bot(); _subscriber(app)
    chat, tenant = _capture(monkeypatch)
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        ne.save_rules(1, {"near_expiry__enabled": "1",
                          "near_expiry__channels": ["telegram", "sms", "whatsapp"]},
                      only_keys=["near_expiry"])
        r = ne.notify_event("near_expiry", tenant_id=1, subscriber=_sub(app), context={"days": 2})
    # telegram delivered; sms/whatsapp not configured → cleanly not sent (no crash)
    assert r.sent.get("telegram") is True
    assert r.sent.get("sms") is False and r.sent.get("whatsapp") is False
    assert r.fired is True                               # attempted, never raised


# ════════════ (3) أحداث جديدة (إيقاف/إعادة تفعيل) ════════════
def test_new_disable_reactivate_events_exist(app):
    from app.radius.services import notifications_engine as ne
    assert "subscriber_disabled" in ne.EVENTS
    assert "subscriber_reactivated" in ne.EVENTS
    assert "telegram" in ne.EVENTS["subscriber_disabled"].channels


# ════════════ (4) الصفحة الموحّدة + الحفظ المقصور ════════════
def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def test_center_page_renders_engine_rules(app):
    c = _client(app)
    html = c.get("/admin/radius/subscriber-notifications").get_data(as_text=True)
    assert "إشعارات المشتركين" in html
    assert "near_expiry" in html and "تنبيه قرب الانتهاء" in html  # engine event
    assert "sn-tmpl" in html                                       # template editor
    assert "router_down" not in html                              # network excluded


def test_center_save_is_scoped_no_network_clobber(app):
    c = _client(app)
    c.get("/admin/radius/subscriber-notifications")               # mint CSRF
    with c.session_transaction() as s:
        tok = s.get("_csrf_token")
    c.post("/admin/radius/subscriber-notifications",
           data={"_csrf_token": tok or "", "near_expiry__enabled": "1",
                 "near_expiry__channels": ["telegram"]})
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        assert ne.load_rule(1, "near_expiry").channels == ["telegram"]
        # network event NOT touched → keeps its default (enabled, telegram)
        rd = ne.load_rule(1, "router_down")
        assert rd.enabled is True and rd.channels == ["telegram"]


def test_communications_notifications_redirects_to_center(app):
    c = _client(app)
    r = c.get("/admin/radius/communications/notifications", follow_redirects=False)
    assert r.status_code in (301, 302)
    assert "/admin/radius/subscriber-notifications" in r.headers.get("Location", "")


# ════════════ (5) ربط مواقع الأحداث على notifications_engine ════════════
def test_users_callsites_fire_notify_event(app, monkeypatch):
    calls = []
    from app.radius.services import notifications_engine as ne
    monkeypatch.setattr(ne, "notify_event",
                        lambda key, **kw: calls.append(key) or ne.NotifyOutcome(event_key=key))
    with app.app_context():
        _subscriber(app, username="ux")
        from app.radius.services.users import get_users_service
        svc = get_users_service()
        svc.disable(actor="admin", username="ux")
        svc.enable(actor="admin", username="ux")
    assert "subscriber_disabled" in calls and "subscriber_reactivated" in calls


def test_accounting_payment_fires_notify_event(app, monkeypatch):
    calls = []
    from app.radius.services import notifications_engine as ne
    monkeypatch.setattr(ne, "notify_event",
                        lambda key, **kw: calls.append((key, kw.get("context")))
                        or ne.NotifyOutcome(event_key=key))
    # find_subscriber is also imported in accounting; keep it real (returns None ok)
    with app.app_context():
        _subscriber(app, username="payer")
        from app.radius.services.accounting import AccountingService
        from app.radius.db.repos import plans_repo  # noqa: F401 (ensure module import ok)
        # Minimal payment via the service; tolerate plan resolution differences
        try:
            AccountingService(tenant_id=1).create_payment(
                actor="admin",
                body={"username": "payer", "amount": "20", "currency": "ILS"})
        except Exception:
            pass
    assert any(k == "payment_received" for k, _ in calls)
