"""Parity-b (2026-10-02): app↔web field parity fixes on the API side.

Each test pins a field the app could edit (or save) that the web treats
differently: settings validation, recycle-bin restore by table name, Telegram
per-event switch vs the channel matrix, payment method whitelist + attribution,
WhatsApp partial toggles, wallet currency, invoice subscriber resolution.
"""
from __future__ import annotations

import secrets

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _patch_setting(client, key, value):
    return client.patch("/api/v1/settings", json={"settings": {key: value}}, headers=AUTH)


def _setting(app, key):
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        return tenants_repo.get_setting(1, key, None)


@pytest.mark.parametrize("key,bad", [
    ("billing.currency", "شيكل"),
    ("billing.currency", "ABC"),
    ("branding.primary_color", "red;}body{display:none"),
    ("comms.country_dial_code", "hello"),
    ("network.radius_server_ip", '1.2.3.4 "><x'),
    ("device_limit.subscribers.mode", "رفض"),
    ("device_limit.cards.count", "0"),
    ("security.unauthorized_ui", "اخفاء"),
    ("portal.show_usage", "maybe"),
])
def test_settings_api_rejects_what_the_web_cannot_store(client, key, bad):
    r = _patch_setting(client, key, bad)
    assert r.status_code == 422, (key, r.get_json())


@pytest.mark.parametrize("key,raw,stored", [
    ("billing.currency", "ils", "ILS"),
    ("comms.country_dial_code", "970", "+970"),
    ("security.block_random_mac_subscribers", "yes", "1"),
    ("portal.show_usage", "نعم", "1"),
    ("cards.login_without_password_default", "false", "0"),
    ("branding.primary_color", "#2BAACC", "#2BAACC"),
])
def test_settings_api_normalises_like_the_web(app, client, key, raw, stored):
    r = _patch_setting(client, key, raw)
    assert r.status_code == 200, r.get_json()
    items = client.get("/api/v1/settings", headers=AUTH).get_json()["data"]["items"]
    assert {i["key"]: i["value"] for i in items}[key] == stored


def test_recycle_bin_restore_accepts_the_listed_table_name(app, client):
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import now_iso
    code = "PB" + secrets.token_hex(3)
    with app.app_context():
        with transaction() as conn:
            cur = conn.execute(
                "INSERT INTO access_plans(tenant_id, name, code, plan_type, service_type, "
                "duration_minutes, validity_days, speed_down_kbps, speed_up_kbps, price, "
                "currency, enabled, created_at, deleted_at) VALUES(1,?,?,'time','Hotspot',"
                "60,1,1000,1000,1,'ILS',1,?,?)", (code, code, now_iso(), now_iso()))
            pid = cur.lastrowid
    listed = client.get("/api/v1/recycle-bin?entity_type=plans", headers=AUTH).get_json()
    types = {i["entity_type"] for i in listed["data"]["items"] if i["id"] == pid}
    assert types == {"access_plans"}
    r = client.post(f"/api/v1/recycle-bin/access_plans/{pid}/restore", headers=AUTH)
    assert r.status_code == 200, r.get_json()


def test_telegram_switch_follows_the_channel_matrix(app, client):
    from app.radius.services import admin_alerts
    key = admin_alerts.ALERTS[0].key
    with app.app_context():
        admin_alerts.set_channels(1, key, ["bell", "telegram", "push"])
    r = client.post(f"/api/v1/alerts/telegram/alerts/{key}/toggle", json={"enabled": False},
                    headers=AUTH)
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        chans = admin_alerts.channels_for(1, key)
        assert "telegram" not in chans and "push" in chans
        assert admin_alerts.is_enabled(1, key) is False
    client.post(f"/api/v1/alerts/telegram/alerts/{key}/toggle", json={"enabled": True},
                headers=AUTH)
    with app.app_context():
        assert "telegram" in admin_alerts.channels_for(1, key)


def _sub(client) -> dict:
    u = "pbs_" + secrets.token_hex(4)
    r = client.post("/api/v1/accounts", json={"username": u, "password": "pw1234",
                                              "plan_id": 1}, headers=AUTH)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]


def test_payment_method_whitelist(client):
    s = _sub(client)
    r = client.post("/api/v1/payments", json={"username": s["username"], "amount": 5,
                                              "method": "bitcoin"}, headers=AUTH)
    assert r.status_code == 422, r.get_json()


def test_whatsapp_partial_toggles_keep_the_others(app, client):
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        tenants_repo.set_setting(1, "whatsapp.send.expiry", "1")
        tenants_repo.set_setting(1, "whatsapp.send.otp", "1")
    r = client.patch("/api/v1/whatsapp/settings", json={"toggles": {"otp": False}},
                     headers=AUTH)
    if r.status_code == 405:
        r = client.post("/api/v1/whatsapp/settings", json={"toggles": {"otp": False}},
                        headers=AUTH)
    assert r.status_code == 200, r.get_json()
    assert _setting(app, "whatsapp.send.otp") == "0"
    assert _setting(app, "whatsapp.send.expiry") == "1"


def test_wallet_currency_is_a_supported_code(client):
    r = client.post("/api/v1/finance/wallets", json={"owner_type": "company", "currency": "ils"},
                    headers=AUTH)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["data"]["wallet"]["currency"] == "ILS"
    r = client.post("/api/v1/finance/wallets", json={"owner_type": "company", "currency": "XYZ"},
                    headers=AUTH)
    assert r.status_code == 422, r.get_json()


def test_invoice_subscriber_is_resolved_like_the_web(client):
    s = _sub(client)
    r = client.post("/api/v1/invoices", json={"subscriber_id": 999999, "username": "x",
                                              "amount": 5}, headers=AUTH)
    assert r.status_code == 422, r.get_json()
    r = client.post("/api/v1/invoices", json={"subscriber_id": s["id"], "username": "someone_else",
                                              "amount": 5}, headers=AUTH)
    assert r.status_code == 422, r.get_json()
    r = client.post("/api/v1/invoices", json={"subscriber_id": s["id"], "amount": 5},
                    headers=AUTH)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["data"]["username"] == s["username"]
