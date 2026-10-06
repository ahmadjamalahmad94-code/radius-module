# -*- coding: utf-8 -*-
"""Owner's dead-field decisions 2026-10-06 — settings catalog (team settings).

REMOVE: ``billing.tax_pct`` / ``auth.allow_password_reset`` (no reader) leave
the web page and ``GET /api/v1/settings``; the keys the web already hid
(``quota.threshold_alerts`` + three ``portal.*``) leave the API list too.
``PATCH`` still accepts all of them silently (old app builds) and writes
nothing.

WIRE: ``cards.default_username_length`` / ``default_password_length`` are what
the generator uses when the batch form / API call names no length (it used to
hardcode 8 / 6) — through the service, the web form and the API.
"""
from __future__ import annotations

import itertools
import os
import re

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}

REMOVED = ("billing.tax_pct", "auth.allow_password_reset")
HIDDEN = ("quota.threshold_alerts", "portal.allow_password_change",
          "portal.allow_self_purchase", "portal.allow_plan_change")


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fields_settings.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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
        sess["_csrf_token"] = "fs-csrf"


def _db():
    from app.radius.db.connection import db
    return db()


def _setting(app, key):
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        return tenants_repo.get_setting(1, key, None)


def _set(app, key, value):
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        tenants_repo.set_setting(1, key, value, by=1)


_seq = itertools.count(1)


def _plan_id() -> int:
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,?,60,1,1.0,'ILS',2048,2048,0,"
        "datetime('now'),datetime('now'))", ("باقة-fs-%d" % next(_seq),))
    return int(cur.lastrowid)


# ── REMOVE / hide ────────────────────────────────────────────────────────────

def test_api_settings_list_has_no_removed_or_hidden_keys(app):
    c = app.test_client()
    data = c.get("/api/v1/settings", headers=AUTH).get_json()["data"]
    keys = {i["key"] for i in data["items"]}
    for key in REMOVED + HIDDEN:
        assert key not in keys, key
        assert key not in data["settings"], key
    assert "cards.default_username_length" in keys          # still listed


def test_api_patch_accepts_removed_keys_silently_and_writes_nothing(app):
    c = app.test_client()
    body = {"settings": {"billing.tax_pct": "16", "auth.allow_password_reset": "0",
                         "quota.threshold_alerts": "1,2",
                         "portal.allow_plan_change": "1",
                         "system.name": "FieldsNet"}}
    r = c.patch("/api/v1/settings", json=body, headers=AUTH)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert set(data["ignored"]) == {"billing.tax_pct", "auth.allow_password_reset",
                                    "quota.threshold_alerts", "portal.allow_plan_change"}
    assert data["updated"] == {"system.name": "FieldsNet"}
    for key in ("billing.tax_pct", "auth.allow_password_reset",
                "quota.threshold_alerts", "portal.allow_plan_change"):
        assert _setting(app, key) is None, key


def test_web_settings_page_has_no_tax_or_password_reset_and_post_ignores_them(app):
    with app.test_client() as c:
        _login(c)
        html = c.get("/admin/radius/settings").get_data(as_text=True)
        assert 'name="billing.tax_pct"' not in html
        assert 'name="auth.allow_password_reset"' not in html
        r = c.post("/admin/radius/settings", data={
            "_csrf_token": "fs-csrf", "billing.tax_pct": "16",
            "auth.allow_password_reset": "0", "system.name": "Web"})
    assert r.status_code in (302, 303)
    assert _setting(app, "billing.tax_pct") is None
    assert _setting(app, "auth.allow_password_reset") is None
    assert _setting(app, "system.name") == "Web"


# ── WIRE: card default lengths ───────────────────────────────────────────────

def test_generator_uses_the_network_default_lengths(app):
    _set(app, "cards.default_username_length", "11")
    _set(app, "cards.default_password_length", "9")
    from app.radius.services.cards import get_cards_service
    with app.app_context():
        batch, cards = get_cards_service().generate_batch(
            actor="admin", plan_id=_plan_id(), count=4, package_name="أطوال")
    assert batch.username_length == 11 and batch.password_length == 9
    assert all(len(c.username) == 11 for c in cards), [c.username for c in cards]
    assert all(len(c.password) == 9 for c in cards), [c.password for c in cards]


def test_explicit_length_still_wins_over_the_default(app):
    _set(app, "cards.default_username_length", "11")
    from app.radius.services.cards import get_cards_service
    with app.app_context():
        batch, cards = get_cards_service().generate_batch(
            actor="admin", plan_id=_plan_id(), count=2, username_length=6,
            password_length=4, package_name="صريح")
    assert all(len(c.username) == 6 and len(c.password) == 4 for c in cards)


def test_api_generate_without_lengths_follows_the_setting(app):
    _set(app, "cards.default_username_length", "10")
    _set(app, "cards.default_password_length", "7")
    with app.app_context():
        pid = _plan_id()
    c = app.test_client()
    r = c.post("/api/v1/cards/generate", headers=AUTH,
               json={"plan_id": pid, "count": 3, "password_generation_type": "digits"})
    assert r.status_code in (200, 201), r.get_json()
    with app.app_context():
        row = _db().execute(
            "SELECT id, username_length, password_length FROM card_batches "
            "ORDER BY id DESC LIMIT 1").fetchone()
        cards = [dict(x) for x in _db().execute(
            "SELECT username, password FROM cards WHERE batch_id = ?", (row["id"],))]
    assert (row["username_length"], row["password_length"]) == (10, 7)
    assert all(len(x["username"]) == 10 and len(x["password"]) == 7 for x in cards), cards
    meta = c.get("/api/v1/cards/batches", headers=AUTH).get_json()["data"]["meta"]
    assert meta["card_defaults"] == {"username_length": 10, "password_length": 7}


def test_web_form_prefills_and_blank_falls_back_to_the_setting(app):
    _set(app, "cards.default_username_length", "12")
    _set(app, "cards.default_password_length", "5")
    with app.app_context():
        pid = _plan_id()
    with app.test_client() as c:
        _login(c)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
        tag = re.search(r'<input[^>]*name="username_length"[^>]*>', html).group(0)
        assert 'value="12"' in tag, tag
        tag = re.search(r'<input[^>]*name="password_length"[^>]*>', html).group(0)
        assert 'value="5"' in tag, tag
        res = c.post("/admin/radius/cards/generate", data={
            "_csrf_token": "fs-csrf", "plan_id": str(pid), "count": "3",
            "batch_type": "printed", "username_length": "", "password_length": "",
            "password_generation_type": "digits"})
    assert res.status_code in (302, 303)
    with app.app_context():
        row = _db().execute("SELECT id FROM card_batches ORDER BY id DESC LIMIT 1").fetchone()
        cards = [dict(x) for x in _db().execute(
            "SELECT username, password FROM cards WHERE batch_id = ?", (row["id"],))]
    assert cards and all(len(x["username"]) == 12 and len(x["password"]) == 5
                         for x in cards), cards


@pytest.mark.parametrize("key,bad", [
    ("cards.default_username_length", "2"),
    ("cards.default_username_length", "abc"),
    ("cards.default_password_length", "0"),
    ("cards.default_password_length", "99"),
])
def test_default_length_settings_are_validated(app, key, bad):
    c = app.test_client()
    r = c.patch("/api/v1/settings", json={"settings": {key: bad}}, headers=AUTH)
    assert r.status_code == 422, r.get_json()
