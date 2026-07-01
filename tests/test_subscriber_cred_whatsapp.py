# -*- coding: utf-8 -*-
"""إرسال بيانات الدخول (المستخدم/البطاقة + كلمة المرور) عبر واتساب — نظير SMS.

يبني على محرّك إشعارات المشترك + قناة واتساب (comms_providers) القائمة، بلا تكرار:

  * «إنشاء مشترك جديد» (subscriber_created): قناة واتساب تُرسل اسم المستخدم
    وكلمة المرور مباشرةً لرقم المشترك (كما تفعل قناة SMS) — لكلٍّ توجّل مستقل.
  * «شراء بطاقة» (store_cards_purchased): قناة واتساب تُرسل رقم البطاقة (المستخدم)
    وكلمة مرورها لرقم المشتري المسجّل (كما تفعل قناة SMS).

الأمان (الجوهر): كلمة المرور حسّاسة — تُرسَل مباشرةً عبر comms_providers.direct_send
(لا يُسجّل جسم الرسالة أبدًا)، ويُسجَّل صفّ تدقيق منقَّح فقط (النتيجة + القناة).

لا شبكة: نقطة الإرسال الوحيدة (comms_providers.http_send) مُموّهة. شغّل الملف وحده."""
from __future__ import annotations

import json
import os

import pytest

from app.radius.services.comms_providers import HttpSendOutcome


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "cred_wa.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "cred-wa-secret")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


# ───────────────────────── helpers ─────────────────────────
def _enable_whatsapp(tenant_id=1):
    """Configure an active WhatsApp channel (generic HTTP gateway)."""
    from app.radius.services import comms_providers
    comms_providers.save_channel_config(tenant_id, "whatsapp", {
        "enabled": "1",
        "send_url_template": "https://gw.example.com/send?to={phone}&text={msg}",
        "http_method": "GET",
    })


class _HttpSpy:
    """Captures comms_providers.http_send calls, fakes a 2xx success — no network."""

    def __init__(self, ok=True):
        self.calls = []
        self._ok = ok

    def __call__(self, *, template, method, phone, message, **kwargs):
        self.calls.append({"phone": phone, "message": message, "template": template})
        return HttpSendOutcome(ok=self._ok, status_code=(200 if self._ok else 500),
                               body_excerpt="ok", error=("" if self._ok else "boom"),
                               final_url="https://gw.example.com")


def _spy_http(monkeypatch, ok=True):
    from app.radius.services import comms_providers
    spy = _HttpSpy(ok=ok)
    monkeypatch.setattr(comms_providers, "http_send", spy)
    return spy


def _subscriber(*, username="shop1", password="Pa55wd", mobile="0599123456"):
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT INTO subscribers(tenant_id,username,password,full_name,"
            "mobile,status,created_at) VALUES(1,?,?,'أحمد',?,'enabled','2026-01-01')",
            (username, password, mobile),
        )


# marketplace helpers (mirror test_store_movement_notifications)
def _market():
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    return CardUsersMarketplaceService(tenant_id=1)


def _plan_id() -> int:
    from app.radius.db.connection import db
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,"
        "price,currency,created_at,updated_at) "
        "VALUES(1,'WA plan',480,1,5.0,'JOD',datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


def _buyer(mobile="0590000111", name="مشتري"):
    return _market().create_card_user(display_name=name, mobile=mobile)


def _package(price="5.00"):
    return _market().create_package(name="WA 8h", plan_id=_plan_id(),
                                    duration_minutes=480, price=price)


def _enable(event_key, channels=("whatsapp",)):
    from app.radius.services import notifications_engine as ne
    ne.save_rules(1, {f"{event_key}__enabled": "1",
                      f"{event_key}__channels": list(channels)},
                  only_keys=[event_key])


# ══════════ (A) subscriber_credentials.send_whatsapp — service ══════════
def test_wa_body_contains_user_and_pass():
    from app.radius.services import subscriber_credentials as sc
    body = sc.build_whatsapp_body("shop1", "Pa55wd")
    assert "shop1" in body and "Pa55wd" in body
    assert "اسم المستخدم" in body and "كلمة المرور" in body


def test_send_whatsapp_includes_user_and_pass(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        _subscriber(password="Cr3ds!")
        spy = _spy_http(monkeypatch)
        from app.radius.services import subscriber_credentials as sc
        from app.radius.db.repos import subscribers_repo

        sub = subscribers_repo.get_subscriber(1, "shop1")
        res = sc.send_whatsapp(1, sub, actor="tester")
        assert res["ok"] is True
        assert len(spy.calls) == 1
        assert "shop1" in spy.calls[0]["message"]
        assert "Cr3ds!" in spy.calls[0]["message"]
        # delivered to the subscriber's own number (normalized with dial code)
        assert "599123456" in spy.calls[0]["phone"]


def test_send_whatsapp_no_mobile_clear_status(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        _subscriber(username="nomob", mobile="")
        spy = _spy_http(monkeypatch)
        from app.radius.services import subscriber_credentials as sc
        from app.radius.db.repos import subscribers_repo

        res = sc.send_whatsapp(1, subscribers_repo.get_subscriber(1, "nomob"), actor="t")
        assert res["ok"] is False
        assert res["reason"] == "no_mobile"
        assert res["error_ar"] == sc.ERR_NO_MOBILE
        assert spy.calls == []  # nothing attempted


def test_send_whatsapp_not_connected_clear_status(app, monkeypatch):
    with app.app_context():
        # No _enable_whatsapp() → the tenant's WhatsApp channel isn't configured.
        _subscriber(username="nolink")
        spy = _spy_http(monkeypatch)
        from app.radius.services import subscriber_credentials as sc
        from app.radius.db.repos import subscribers_repo

        res = sc.send_whatsapp(1, subscribers_repo.get_subscriber(1, "nolink"), actor="t")
        assert res["ok"] is False
        assert res["reason"] == "not_connected"
        assert res["error_ar"] == sc.ERR_WA_NOT_CONNECTED
        assert spy.calls == []


def test_wa_password_never_in_audit(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        _subscriber(password="TopSecret9")
        _spy_http(monkeypatch)
        from app.radius.services import subscriber_credentials as sc
        from app.radius.services.audit import get_audit_service
        from app.radius.db.repos import subscribers_repo

        sc.send_whatsapp(1, subscribers_repo.get_subscriber(1, "shop1"), actor="tester")
        rows = [r for r in get_audit_service().recent(limit=50)
                if r.action == "subscriber.credentials_whatsapp"]
        assert rows, "expected a redacted whatsapp-credentials audit row"
        for r in rows:
            assert r.payload.get("channel") == "whatsapp"
            assert "TopSecret9" not in json.dumps(r.payload, ensure_ascii=False)


# ══════════ (B) engine — subscriber_created over WhatsApp ══════════
def test_default_channels_include_sms_and_whatsapp(app):
    from app.radius.services import notifications_engine as ne
    ev = ne.EVENTS["subscriber_created"]
    assert ev.sends_credentials is True
    assert set(ev.channels) == {"sms", "whatsapp"}  # both credential channels default-on
    assert "{password}" not in ev.template  # shared template stays password-free


def test_subscriber_created_whatsapp_sends_credentials(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        _subscriber(password="Wp@ss1")
        spy = _spy_http(monkeypatch)
        from app.radius.services import notifications_engine as ne

        # Operator enables ONLY the WhatsApp channel for «subscriber_created».
        _enable("subscriber_created", channels=("whatsapp",))
        sub = ne.find_subscriber(1, username="shop1")
        out = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)
        assert out.fired is True
        assert out.sent.get("whatsapp") is True
        # The WhatsApp body carried BOTH the username and the cleartext password.
        assert len(spy.calls) == 1
        assert "shop1" in spy.calls[0]["message"]
        assert "Wp@ss1" in spy.calls[0]["message"]


def test_subscriber_created_sms_toggle_leaves_whatsapp_untouched(app, monkeypatch):
    """Per-channel gating: enabling ONLY sms must not fire WhatsApp."""
    with app.app_context():
        _enable_whatsapp()
        _subscriber()
        spy = _spy_http(monkeypatch)
        from app.radius.services import notifications_engine as ne

        _enable("subscriber_created", channels=("sms",))  # whatsapp OFF
        sub = ne.find_subscriber(1, username="shop1")
        out = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)
        # WhatsApp never attempted (no http_send); sms not connected here → no-op.
        assert "whatsapp" not in out.channels
        assert spy.calls == []


def test_subscriber_created_disabled_no_whatsapp(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        _subscriber()
        spy = _spy_http(monkeypatch)
        from app.radius.services import notifications_engine as ne

        ne.save_rules(1, {"subscriber_created__enabled": "0",
                          "subscriber_created__channels": ["whatsapp"]},
                      only_keys=["subscriber_created"])
        sub = ne.find_subscriber(1, username="shop1")
        out = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)
        assert out.fired is False
        assert out.reason == "disabled"
        assert spy.calls == []


# ══════════ (C) store_cards_purchased over WhatsApp ══════════
def test_send_cards_whatsapp_body_has_card_creds(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        spy = _spy_http(monkeypatch)
        from app.radius.services import store_movement_notifications as smn
        from types import SimpleNamespace

        rec = SimpleNamespace(mobile="0590000999")
        res = smn.send_cards_credentials_whatsapp(
            1, rec, [{"username": "mp01", "password": "48217390"}], actor="qa")
        assert res["ok"] is True
        assert len(spy.calls) == 1
        assert "mp01" in spy.calls[0]["message"]
        assert "48217390" in spy.calls[0]["message"]


def test_send_cards_whatsapp_no_mobile(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        spy = _spy_http(monkeypatch)
        from app.radius.services import store_movement_notifications as smn
        from types import SimpleNamespace

        res = smn.send_cards_credentials_whatsapp(
            1, SimpleNamespace(mobile=""), [{"username": "x", "password": "y"}])
        assert res["ok"] is False
        assert res["reason"] == "no_mobile"
        assert spy.calls == []


def test_purchase_sends_card_creds_whatsapp_to_registered_mobile(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        _enable("store_cards_purchased", channels=("whatsapp",))
        spy = _spy_http(monkeypatch)
        b = _buyer(mobile="0590000222")
        pkg = _package()
        _market().recharge_wallet(card_user_id=b["id"], amount="10.00", actor="qa")
        purchase = _market().purchase_package(
            card_user_id=b["id"], package_id=pkg["id"], actor="qa")
    # the WhatsApp message carried the purchased card's username + password
    assert len(spy.calls) == 1
    assert purchase["cred_username"] in spy.calls[0]["message"]
    assert purchase["cred_password"] in spy.calls[0]["message"]
    # delivered to the buyer's registered number (normalized with dial code)
    assert "590000222" in spy.calls[0]["phone"]


def test_card_password_never_in_whatsapp_audit(app, monkeypatch):
    with app.app_context():
        _enable_whatsapp()
        _enable("store_cards_purchased", channels=("whatsapp",))
        _spy_http(monkeypatch)
        b = _buyer(mobile="0590000333")
        pkg = _package()
        _market().recharge_wallet(card_user_id=b["id"], amount="10.00", actor="qa")
        purchase = _market().purchase_package(
            card_user_id=b["id"], package_id=pkg["id"], actor="qa")
        from app.radius.services.audit import get_audit_service
        rows = [r for r in get_audit_service().recent(limit=50)
                if r.action == "store.cards_credentials_whatsapp"]
        assert rows, "expected a redacted whatsapp cards-creds audit row"
        for r in rows:
            blob = json.dumps(r.payload, ensure_ascii=False)
            assert purchase["cred_password"] not in blob
            assert "password" not in r.payload  # only a count is kept
            assert r.payload.get("channel") == "whatsapp"


def test_purchase_whatsapp_toggle_off_no_send(app, monkeypatch):
    """Disabling the event gates the WhatsApp card-creds send entirely."""
    with app.app_context():
        _enable_whatsapp()
        from app.radius.services import notifications_engine as ne
        ne.save_rules(1, {"store_cards_purchased__enabled": "0",
                          "store_cards_purchased__channels": ["whatsapp"]},
                      only_keys=["store_cards_purchased"])
        spy = _spy_http(monkeypatch)
        b = _buyer(mobile="0590000444")
        pkg = _package()
        _market().recharge_wallet(card_user_id=b["id"], amount="10.00", actor="qa")
        _market().purchase_package(card_user_id=b["id"], package_id=pkg["id"], actor="qa")
    assert spy.calls == []  # no WhatsApp attempted at all
