# -*- coding: utf-8 -*-
"""إشعارات حركات متجر البطاقات الإلكتروني (شحن/سحب/شراء) للمشتري.

تبني على notifications_engine + محوّل TweetSMS + منطق 60 حرفًا — بلا تكرار:
  * شحن رصيد   → store_balance_recharge  (تأكيد إيداع / شحن المدير)
  * سحب رصيد   → store_balance_withdraw  (تأكيد سحب)
  * شراء بطاقات → store_cards_purchased   (يُرسل بيانات الدخول SMS لرقم المشتري)

لا شبكة: نقطة HTTP الوحيدة (tweetsms._http_get) مُموّهة. شغّل الملف وحده."""
from __future__ import annotations

import json
import os
import urllib.parse

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "store_movement.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "store-mv-secret")
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
def _connect_sms(api_key="K", sender="HOBE"):
    from app.radius.db.repos import tenant_sms_settings_repo
    tenant_sms_settings_repo.upsert(tenant_id=1, api_key=api_key, sender=sender, enabled=True)


def _capture_http(monkeypatch):
    captured = {}
    from app.radius.services import tweetsms

    def _fake_get(url, timeout=12.0):
        captured["url"] = url
        captured["message"] = urllib.parse.unquote_plus(
            dict(urllib.parse.parse_qsl(url.split("?", 1)[1])).get("message", "")
        )
        return True, 200, "1:42:972599123456", ""

    monkeypatch.setattr(tweetsms, "_http_get", _fake_get)
    return captured


def _market():
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    return CardUsersMarketplaceService(tenant_id=1)


def _plan_id() -> int:
    from app.radius.db.connection import db
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,"
        "price,currency,created_at,updated_at) "
        "VALUES(1,'MV plan',480,1,5.0,'JOD',datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


def _buyer(mobile="0590000111", name="مشتري"):
    return _market().create_card_user(display_name=name, mobile=mobile)


def _package(price="5.00"):
    return _market().create_package(name="MV 8h", plan_id=_plan_id(),
                                    duration_minutes=480, price=price)


def _enable(event_key, channels=("sms",)):
    from app.radius.services import notifications_engine as ne
    ne.save_rules(1, {f"{event_key}__enabled": "1",
                      f"{event_key}__channels": list(channels)},
                  only_keys=[event_key])


# ═══════════════ (1) registry: new store events ═══════════════
def test_store_events_registered_in_store_group(app):
    from app.radius.services import notifications_engine as ne
    for key in ("store_balance_recharge", "store_balance_withdraw", "store_cards_purchased"):
        assert key in ne.EVENTS
        assert ne.EVENTS[key].group == "store"
    assert ne.EVENTS["store_cards_purchased"].sends_card_credentials is True
    # recharge/withdraw do NOT send credentials (plain movement messages)
    assert ne.EVENTS["store_balance_recharge"].sends_card_credentials is False
    assert ne.GROUP_LABELS["store"] == "إشعارات متجر البطاقات الإلكتروني"


# ═══════════════ (2) card-credentials SMS body ═══════════════
def test_cards_body_single_card_has_user_and_pass(app):
    from app.radius.services import store_movement_notifications as smn
    body = smn.build_cards_sms_body([{"username": "mp01", "password": "12345678"}])
    assert "mp01" in body and "12345678" in body
    assert "المستخدم" in body and "كلمة المرور" in body


def test_cards_body_empty_when_no_creds(app):
    from app.radius.services import store_movement_notifications as smn
    assert smn.build_cards_sms_body([]) == ""
    assert smn.build_cards_sms_body([{"username": "", "password": ""}]) == ""


def test_cards_body_single_fits_one_segment(app):
    from app.radius.services import store_movement_notifications as smn
    from app.radius.services import sms_segments
    body = smn.build_cards_sms_body([{"username": "mp000123", "password": "48217390"}])
    assert sms_segments.analyze(body).segments == 1


# ═══════════════ (3) action sites fire EXACTLY ONCE ═══════════════
def test_deposit_confirm_fires_recharge_once(app, monkeypatch):
    calls = []
    from app.radius.services import notifications_engine as ne
    monkeypatch.setattr(ne, "notify_event",
                        lambda key, **kw: calls.append((key, kw.get("context")))
                        or ne.NotifyOutcome(event_key=key))
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        b = _buyer()
        dep = DepositRequestService(tenant_id=1)
        req = dep.create_request(card_user_id=b["id"], amount_claimed="20.00", method="cash")
        dep.confirm(req["id"], actor="qa")
    recharge = [c for c in calls if c[0] == "store_balance_recharge"]
    assert len(recharge) == 1
    assert "20" in recharge[0][1]["amount"]
    assert "balance" in recharge[0][1]


def test_withdraw_confirm_fires_withdraw_once(app, monkeypatch):
    calls = []
    from app.radius.services import notifications_engine as ne
    monkeypatch.setattr(ne, "notify_event",
                        lambda key, **kw: calls.append((key, kw.get("context")))
                        or ne.NotifyOutcome(event_key=key))
    with app.app_context():
        from app.radius.services.store_withdrawals import WithdrawalRequestService
        b = _buyer()
        _market().recharge_wallet(card_user_id=b["id"], amount="30.00", actor="qa")
        wd = WithdrawalRequestService(tenant_id=1)
        req = wd.create_request(card_user_id=b["id"], amount="20.00",
                                payee_name="x", payee_account="ACC")
        wd.confirm(req["id"], actor="qa")
    withdraws = [c for c in calls if c[0] == "store_balance_withdraw"]
    assert len(withdraws) == 1
    assert "20" in withdraws[0][1]["amount"]


def test_purchase_fires_cards_purchased_once_with_creds_in_context(app, monkeypatch):
    calls = []
    from app.radius.services import notifications_engine as ne
    monkeypatch.setattr(ne, "notify_event",
                        lambda key, **kw: calls.append((key, kw.get("context")))
                        or ne.NotifyOutcome(event_key=key))
    with app.app_context():
        b = _buyer()
        pkg = _package()
        _market().recharge_wallet(card_user_id=b["id"], amount="10.00", actor="qa")
        _market().purchase_package(card_user_id=b["id"], package_id=pkg["id"], actor="qa")
    purchased = [c for c in calls if c[0] == "store_cards_purchased"]
    assert len(purchased) == 1
    ctx = purchased[0][1]
    assert ctx["count"] == "1"
    assert ctx["cards"] and ctx["cards"][0]["username"] and ctx["cards"][0]["password"]


# ═══════════════ (4) card-purchase sends creds SMS to registered number ═══════════════
def test_purchase_sends_card_creds_sms_to_registered_mobile(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _enable("store_cards_purchased", channels=("sms",))
        cap = _capture_http(monkeypatch)
        b = _buyer(mobile="0590000222")
        pkg = _package()
        _market().recharge_wallet(card_user_id=b["id"], amount="10.00", actor="qa")
        purchase = _market().purchase_package(
            card_user_id=b["id"], package_id=pkg["id"], actor="qa")
    # the SMS carried the purchased card's username + password
    assert purchase["cred_username"] in cap["message"]
    assert purchase["cred_password"] in cap["message"]
    # delivered to the buyer's registered number (normalized with dial code)
    assert "590000222" in cap["url"]


def test_card_password_never_logged_in_audit(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _enable("store_cards_purchased", channels=("sms",))
        _capture_http(monkeypatch)
        b = _buyer(mobile="0590000333")
        pkg = _package()
        _market().recharge_wallet(card_user_id=b["id"], amount="10.00", actor="qa")
        purchase = _market().purchase_package(
            card_user_id=b["id"], package_id=pkg["id"], actor="qa")
        from app.radius.services.audit import get_audit_service
        rows = [r for r in get_audit_service().recent(limit=50)
                if r.action == "store.cards_credentials_sms"]
        assert rows, "expected a redacted cards-sms audit row"
        for r in rows:
            blob = json.dumps(r.payload, ensure_ascii=False)
            assert purchase["cred_password"] not in blob
            assert "password" not in r.payload  # only a count is kept


# ═══════════════ (5) gating: disabled event sends nothing ═══════════════
def test_disabled_cards_event_no_sms(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        from app.radius.services import notifications_engine as ne
        ne.save_rules(1, {"store_cards_purchased__enabled": "0",
                          "store_cards_purchased__channels": ["sms"]},
                      only_keys=["store_cards_purchased"])
        cap = _capture_http(monkeypatch)
        b = _buyer(mobile="0590000444")
        pkg = _package()
        _market().recharge_wallet(card_user_id=b["id"], amount="10.00", actor="qa")
        _market().purchase_package(card_user_id=b["id"], package_id=pkg["id"], actor="qa")
    assert "message" not in cap  # no SMS attempted at all


def test_recharge_sms_uses_tweetsms_when_enabled(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _enable("store_balance_recharge", channels=("sms",))
        cap = _capture_http(monkeypatch)
        from app.radius.services.store_deposits import DepositRequestService
        b = _buyer(mobile="0590000555")
        dep = DepositRequestService(tenant_id=1)
        req = dep.create_request(card_user_id=b["id"], amount_claimed="25.00", method="cash")
        dep.confirm(req["id"], actor="qa")
    assert "25" in cap.get("message", "")          # movement SMS via TweetSMS
    assert "590000555" in cap.get("url", "")


# ═══════════════ (6) page renders store section + persists ═══════════════
def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1,
                 admin_name="t", _csrf_token="mv-csrf")
    return c


def test_subscriber_notifications_page_renders_store_section(app):
    c = _client(app)
    html = c.get("/admin/radius/subscriber-notifications").get_data(as_text=True)
    assert "إشعارات متجر البطاقات الإلكتروني" in html
    assert "store_cards_purchased" in html
    assert "store_balance_recharge" in html and "store_balance_withdraw" in html


def test_store_toggles_persist(app):
    c = _client(app)
    c.get("/admin/radius/subscriber-notifications")
    c.post("/admin/radius/subscriber-notifications",
           data={"_csrf_token": "mv-csrf",
                 "store_cards_purchased__enabled": "1",
                 "store_cards_purchased__channels": ["sms"]})
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        rule = ne.load_rule(1, "store_cards_purchased")
        assert rule.enabled is True
        assert rule.channels == ["sms"]
