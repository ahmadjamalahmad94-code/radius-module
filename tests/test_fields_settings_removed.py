# -*- coding: utf-8 -*-
"""Owner's dead-field decisions 2026-10-06 (team settings) — REMOVE.

Each removed field: gone from the web form, the save path no longer writes it,
and the API still accepts the key from old app builds (no 422) but ignores it.
Covers: admin avatar_url · collection auto_apply · proof «صورة» · the custom
SMS HTTP channel (SMS = TweetSMS) + WhatsApp mode/balance_url · SaaS voucher
plan_id · service rent_per_month · share-group quota/speeds/max_members.
"""
from __future__ import annotations

import os

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fields_removed.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_COLLECTION_FORCE_OPEN", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    return flask_app


def _login(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "owner_root"
        sess["admin_name"] = "Owner"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "fr-csrf"


def _db():
    from app.radius.db.connection import db
    return db()


def _setting(app, key):
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        return tenants_repo.get_setting(1, key, None)


# ── admin avatar_url ────────────────────────────────────────────────────────

def test_admin_avatar_url_gone_from_forms_and_ignored_by_api(app):
    c = app.test_client()
    r = c.post("/api/v1/admins", headers=AUTH, json={
        "username": "av_mgr", "password": "x12345678", "full_name": "A",
        "avatar_url": "https://evil.example/a.png"})
    assert r.status_code in (200, 201), r.get_json()
    aid = r.get_json()["data"]["id"] if "id" in r.get_json()["data"] else r.get_json()["data"]["admin"]["id"]
    r = c.patch(f"/api/v1/admins/{aid}", headers=AUTH,
                json={"avatar_url": "https://x.example/b.png", "full_name": "B"})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        row = _db().execute("SELECT avatar_url, full_name FROM admins WHERE id = ?",
                            (aid,)).fetchone()
    assert (row["avatar_url"] or "") == "" and row["full_name"] == "B"
    with app.test_client() as w:
        _login(w)
        assert 'name="avatar_url"' not in w.get(f"/admin/radius/admins/{aid}/edit").get_data(as_text=True)
        assert 'name="avatar_url"' not in w.get("/admin/radius/admins").get_data(as_text=True)


# ── payment collection auto_apply + proof «صورة» ──────────────────────────────

def test_collection_auto_apply_is_ignored_and_not_on_the_web_page(app):
    c = app.test_client()
    r = c.patch("/api/v1/payments/settings", headers=AUTH,
                json={"auto_apply": True, "enabled": True, "provider": "manual_wallet",
                      "wallet_number": "0599", "currency": "ILS"})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        row = _db().execute("SELECT auto_apply FROM tenant_payment_settings "
                            "WHERE tenant_id = 1").fetchone()
    assert not row["auto_apply"]
    with app.test_client() as w:
        _login(w)
        html = w.get("/admin/radius/payments/settings").get_data(as_text=True)
    assert 'name="auto_apply"' not in html


def test_proof_type_image_is_recorded_as_a_reference(app):
    c = app.test_client()
    assert c.patch("/api/v1/payments/settings", headers=AUTH, json={
        "enabled": True, "provider": "manual_wallet", "wallet_number": "0599000000",
        "wallet_owner_name": "W", "currency": "ILS", "confirmation_mode": "manual",
        "allow_cards": True}).status_code == 200
    req = c.post("/api/v1/payments/requests", headers=AUTH, json={
        "payer_type": "subscriber", "purpose": "card_purchase", "amount": 20})
    assert req.status_code in (200, 201), req.get_json()
    rid = req.get_json()["data"]["request"]["id"]
    r = c.post(f"/api/v1/payments/requests/{rid}/proofs", headers=AUTH,
               json={"proof_type": "image", "reference_number": "JP-1"})
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        row = _db().execute("SELECT proof_type FROM payment_proofs "
                            "WHERE payment_request_id = ?", (rid,)).fetchone()
    assert row["proof_type"] == "manual_reference"


# ── SMS = TweetSMS; WhatsApp channel without mode / balance_url ──────────────

def test_channels_page_shows_tweetsms_status_and_only_whatsapp_form(app):
    with app.test_client() as w:
        _login(w)
        html = w.get("/admin/radius/communications/channels").get_data(as_text=True)
        assert 'data-testid="channel-sms-tweetsms"' in html
        assert 'data-testid="channel-sms"' not in html
        assert 'data-testid="channel-whatsapp"' in html
        assert 'name="balance_url"' not in html and 'name="mode"' not in html
        r = w.post("/admin/radius/communications/channels", data={
            "_csrf_token": "fr-csrf", "channel": "whatsapp", "enabled": "1",
            "mode": "admin_quota", "balance_url": "https://x/b",
            "send_url_template": "https://gw.example/?to={phone}&t={msg}",
            "http_method": "GET"})
        assert r.status_code in (302, 303)
        w.post("/admin/radius/communications/channels", data={
            "_csrf_token": "fr-csrf", "channel": "sms", "enabled": "1",
            "send_url_template": "https://sms.example/?to={phone}"})
    assert _setting(app, "comms.whatsapp.send_url_template").startswith("https://gw.example")
    for key in ("comms.whatsapp.mode", "comms.whatsapp.balance_url",
                "comms.sms.enabled", "comms.sms.send_url_template"):
        assert _setting(app, key) is None, key


def test_direct_sms_goes_through_tweetsms_not_the_old_http_channel(app, monkeypatch):
    from app.radius.db.repos import tenants_repo
    from app.radius.services import comms_providers, tweetsms
    with app.app_context():
        # A stale custom SMS channel from before the decision must not be used.
        tenants_repo.set_setting(1, "comms.sms.enabled", "1", by=1)
        tenants_repo.set_setting(1, "comms.sms.send_url_template",
                                 "https://old-sms.example/?to={phone}&t={msg}", by=1)
        http_calls, tweet_calls = [], []
        monkeypatch.setattr(comms_providers, "http_send",
                            lambda **kw: http_calls.append(kw) or comms_providers.HttpSendOutcome(ok=True))
        monkeypatch.setattr(tweetsms, "send_sms",
                            lambda tid, to, msg: tweet_calls.append((tid, to, msg)) or {"ok": True})
        ok, err = comms_providers.direct_send(1, "sms", "0599111222", "مرحبا")
    assert ok is True and err == ""
    assert http_calls == [] and tweet_calls == [(1, "0599111222", "مرحبا")]


# ── SaaS: voucher plan · service rent · share-group limits ───────────────────

def _plan_id() -> int:
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,'باقة-كوبون',60,1,1.0,'ILS',2048,2048,0,"
        "datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


def test_voucher_plan_is_ignored_by_api_and_web(app):
    with app.app_context():
        pid = _plan_id()
    c = app.test_client()
    r = c.post("/api/v1/vouchers", headers=AUTH,
               json={"amount": 5, "count": 1, "plan_id": pid})
    assert r.status_code == 201, r.get_json()
    r = c.post("/api/v1/vouchers", headers=AUTH,
               json={"amount": 5, "count": 1, "plan_id": 0})       # old app blank
    assert r.status_code == 201, r.get_json()
    with app.test_client() as w:
        _login(w)
        w.post("/admin/radius/vouchers/generate", data={
            "_csrf_token": "fr-csrf", "count": "1", "amount": "3", "plan_id": str(pid)})
        html = w.get("/admin/radius/finance/billing?tab=vouchers").get_data(as_text=True)
    assert 'name="plan_id"' not in html.split('action="/admin/radius/vouchers/generate"')[-1][:3000]
    with app.app_context():
        rows = _db().execute("SELECT plan_id FROM vouchers").fetchall()
    assert len(rows) == 3 and all(r["plan_id"] is None for r in rows)


def test_service_rent_is_not_written_and_not_shown(app):
    with app.app_context():
        _db().execute("INSERT INTO subscribers(tenant_id, username, password, status, created_at)"
                      " VALUES (1, 'rent-sub', 'x', 'enabled', datetime('now'))")
        sid = _db().execute("SELECT id FROM subscribers WHERE username='rent-sub'").fetchone()["id"]
    c = app.test_client()
    r = c.post("/api/v1/services", headers=AUTH,
               json={"subscriber_id": sid, "name": "Router", "rent_per_month": 40})
    assert r.status_code == 201, r.get_json()
    svc_id = r.get_json()["data"]["id"]
    with app.app_context():
        _db().execute("UPDATE services SET rent_per_month = 25 WHERE id = ?", (svc_id,))
    r = c.patch(f"/api/v1/services/{svc_id}", headers=AUTH,
                json={"subscriber_id": sid, "name": "Router 2", "rent_per_month": 99})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        row = _db().execute("SELECT name, rent_per_month FROM services WHERE id = ?",
                            (svc_id,)).fetchone()
    assert row["name"] == "Router 2" and row["rent_per_month"] == 25   # kept, not written
    with app.test_client() as w:
        _login(w)
        assert 'name="rent_per_month"' not in w.get("/admin/radius/services").get_data(as_text=True)


def test_share_group_limits_are_ignored(app):
    c = app.test_client()
    r = c.post("/api/v1/share-groups", headers=AUTH,
               json={"name": "VIP", "shared_quota_mb": 1024, "max_members": "bad",
                     "shared_speed_down_kbps": 4000})
    assert r.status_code == 201, r.get_json()
    gid = r.get_json()["data"]["id"]
    r = c.patch(f"/api/v1/share-groups/{gid}", headers=AUTH,
                json={"name": "VIP2", "max_members": 7, "shared_speed_up_kbps": 99})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        row = _db().execute("SELECT name, shared_quota_mb, shared_speed_down_kbps,"
                            " shared_speed_up_kbps, max_members FROM share_groups"
                            " WHERE id = ?", (gid,)).fetchone()
    assert row["name"] == "VIP2"
    assert (row["shared_quota_mb"], row["shared_speed_down_kbps"],
            row["shared_speed_up_kbps"], row["max_members"]) == (0, 0, 0, 0)
    with app.test_client() as w:
        _login(w)
        html = w.get(f"/admin/radius/share_groups/{gid}/edit").get_data(as_text=True)
    for name in ("shared_quota_mb", "max_members", "shared_speed_down_kbps"):
        assert f'name="{name}"' not in html, name
