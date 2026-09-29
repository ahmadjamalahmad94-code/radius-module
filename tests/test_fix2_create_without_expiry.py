"""Owner decision 2026-09-29 (integration of fix wave 2): a subscriber created
WITHOUT an expiry follows the per-server setting
``subscribers.create_without_expiry``:

* ``expired`` (default, every server) — born expired: ``expire_at`` = the
  creation moment, so it shows «منتهي» until a payment/extend activates it;
* ``unlimited`` (the free HobeHub server only) — never expires (NULL).

An explicit choice always wins: the web «بدون انتهاء» checkbox and an explicit
``"expire_at": null`` on the API mean NULL; an explicit date is kept. The rule
covers the web form (blank date), ``POST /api/v1/accounts`` (key absent — the
app and HobeHub use this path), and the bulk/import create paths.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fix2-cwe-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
KEY = "subscribers.create_without_expiry"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fix2cwe.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fix2-cwe-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_cwe")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-cwe")
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


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────── helpers ───────────────

def _set_mode(value):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, KEY, value, by=0)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _plan() -> int:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat()
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?)",
        ("p_" + uuid4().hex[:6], 30 * 1440, 30, 30.0, "ILS", now, now))
    return int(cur.lastrowid)


def _web_login(client):
    res = client.post("/admin/radius/login",
                      data={"username": "owner_cwe", "password": "owner-pass-cwe"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _api_create(client, username, **extra):
    res = client.post("/api/v1/accounts", headers=AUTH,
                      json={"username": username, "password": "secret1", **extra})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def _web_create(client, username, **extra):
    data = {"_csrf_token": _csrf(client), "username": username, "password": "pass1",
            "plan_id": str(_plan()), "service_type": "hotspot", "status": "enabled",
            **extra}
    res = client.post("/admin/radius/users", data=data)
    assert res.status_code in {302, 303}, res.status_code
    return _get(username)


def _born_expired(sub, before):
    assert sub is not None
    assert sub.expire_at is not None
    assert before - timedelta(seconds=2) <= sub.expire_at <= datetime.utcnow()


# ─────────────── default = expired ───────────────

def test_default_setting_is_expired(app):
    from app.radius.core.system_config import (_DEFAULTS, create_without_expiry_mode,
                                               default_new_subscriber_expiry)
    assert _DEFAULTS[KEY] == "expired"
    assert create_without_expiry_mode(1) == "expired"
    assert default_new_subscriber_expiry(1) is not None
    _set_mode("garbage")                       # a broken value falls back to the safe default
    assert create_without_expiry_mode(1) == "expired"


def test_api_create_without_expire_at_is_born_expired(client):
    before = datetime.utcnow()
    data = _api_create(client, "api_noexp")
    assert data["expire_at"] is not None
    _born_expired(_get("api_noexp"), before)
    ctx = client.get("/api/v1/accounts/api_noexp", headers=AUTH).get_json()["data"]
    assert ctx["expire_at"] is not None


def test_api_create_with_explicit_null_never_expires(client):
    data = _api_create(client, "api_null", expire_at=None)
    assert data["expire_at"] is None
    assert _get("api_null").expire_at is None


def test_api_create_with_explicit_date_keeps_it(client):
    when = (datetime.utcnow() + timedelta(days=20)).replace(microsecond=0)
    _api_create(client, "api_date", expire_at=when.isoformat() + "Z")
    assert _get("api_date").expire_at == when


def test_web_create_blank_date_is_born_expired(client):
    _web_login(client)
    before = datetime.utcnow()
    _born_expired(_web_create(client, "web_blank"), before)


def test_web_create_with_checkbox_never_expires(client):
    _web_login(client)
    assert _web_create(client, "web_unlim", no_expiry="1").expire_at is None


# ─────────────── unlimited (HobeHub) ───────────────

def test_unlimited_api_create_without_key_never_expires(client):
    _set_mode("unlimited")
    data = _api_create(client, "hh_api")
    assert data["expire_at"] is None
    assert _get("hh_api").expire_at is None


def test_unlimited_web_blank_date_never_expires(client):
    _set_mode("unlimited")
    _web_login(client)
    assert _web_create(client, "hh_web").expire_at is None


def test_unlimited_explicit_date_still_wins(client):
    _set_mode("unlimited")
    when = (datetime.utcnow() + timedelta(days=5)).replace(microsecond=0)
    _api_create(client, "hh_date", expire_at=when.isoformat() + "Z")
    assert _get("hh_date").expire_at == when
    _web_login(client)
    local = when + timedelta(hours=3)          # any explicit date: just not NULL
    sub = _web_create(client, "hh_webdate", expire_year=str(local.year),
                      expire_month=str(local.month), expire_day=str(local.day))
    assert sub.expire_at is not None


def test_expired_explicit_checkbox_and_null_still_win(client):
    _set_mode("expired")
    assert _api_create(client, "ex_null", expire_at=None)["expire_at"] is None
    _web_login(client)
    assert _web_create(client, "ex_web", no_expiry="1").expire_at is None


# ─────────────── bulk / import paths ───────────────

def test_import_paths_follow_the_setting(app):
    from app.radius.services import mt_import_runner

    class _Cand:
        username = "mt_user"
        password = "p"
        service_type = "hotspot"
        plan_id = None
        mac = ""
        static_ip = ""
        disabled = False

    before = datetime.utcnow()
    sub = mt_import_runner._subscriber_from_candidate(1, _Cand())
    assert sub.expire_at is not None and sub.expire_at >= before - timedelta(seconds=2)
    _set_mode("unlimited")
    assert mt_import_runner._subscriber_from_candidate(1, _Cand()).expire_at is None


def test_manager_create_without_activation_follows_the_setting(app):
    from app.radius.services import manager_distributor_ops as mdo
    svc = mdo.ManagerDistributorOpsService(tenant_id=1)
    svc.assert_allowed = lambda **_kw: None
    from app.radius.db.connection import db
    row = db().execute("SELECT id FROM admins ORDER BY id LIMIT 1").fetchone()
    out = svc.create_subscriber_without_activation(manager_id=int(row["id"]),
                                                   username="mgr_new", password="p")
    assert out["subscriber"].expire_at is not None
    _set_mode("unlimited")
    out = svc.create_subscriber_without_activation(manager_id=int(row["id"]),
                                                   username="mgr_new2", password="p")
    assert out["subscriber"].expire_at is None


# ─────────────── the setting itself (web + API) ───────────────

def test_setting_is_on_the_settings_page_and_validated(client):
    _web_login(client)
    html = client.get("/admin/radius/settings").get_data(as_text=True)
    assert 'name="subscribers.create_without_expiry"' in html
    assert "المشترك الجديد بلا تاريخ انتهاء" in html
    assert "منتهٍ فورًا" in html and "بلا انتهاء" in html
    res = client.post("/admin/radius/settings",
                      data={"_csrf_token": _csrf(client), KEY: "unlimited"})
    assert res.status_code in {302, 303}
    from app.radius.core.system_config import create_without_expiry_mode
    assert create_without_expiry_mode(1) == "unlimited"
    client.post("/admin/radius/settings", data={"_csrf_token": _csrf(client), KEY: "forever"})
    assert create_without_expiry_mode(1) == "unlimited"          # bad value refused


def test_setting_via_api_and_exposed_to_the_app(client):
    res = client.patch("/api/v1/settings", headers=AUTH, json={KEY: "sometimes"})
    assert res.status_code == 422
    res = client.patch("/api/v1/settings", headers=AUTH, json={KEY: "unlimited"})
    assert res.status_code == 200, res.get_json()
    got = client.get("/api/v1/settings", headers=AUTH).get_json()["data"]
    assert got["settings"][KEY] == "unlimited"
    assert got["system"]["create_without_expiry"] == "unlimited"
    assert _api_create(client, "api_after_patch")["expire_at"] is None
