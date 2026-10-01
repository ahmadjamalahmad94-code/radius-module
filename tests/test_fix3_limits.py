"""Fix wave 3 — owner decisions 2026-09-30: per-server «الحدود» + 1-year rule on create.

Every cap is a tenant setting read through ONE helper (core.limits) on web,
API and bulk paths; defaults = the old hardcoded values; 0/empty refused;
«بلا حدّ» only via an explicit toggle that keeps the technical ceilings;
messages quote the configured value (365 keeps the owner phrase «سنة»);
system.limits exposed to the app. Create is bound by max_extend_days.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fix3-limits-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
FETCH = {"X-Requested-With": "fetch"}
OWNER_PHRASE = "أقصى تمديد في المرة الواحدة سنة — كرّر التمديد إن احتجت أكثر"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fix3limits.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_CREATE_EXPIRY_ONE_YEAR", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fix3-limits-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_lim")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()

    def _fresh_g():
        from flask import g
        for attr in ("_api_authed", "api_token", "api_token_id", "api_token_scopes",
                     "admin_id", "tenant_id", "_sa_identity", "_sa_identity_key"):
            g.__dict__.pop(attr, None)
    application.before_request_funcs.setdefault(None, []).insert(0, _fresh_g)
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


@pytest.fixture(autouse=True)
def _no_pod(monkeypatch):
    import app.radius.services.policy_reconciler as pr
    monkeypatch.setattr(pr, "reconcile_active_sessions_against_policy",
                        lambda tid, **kw: None)


def _db():
    from app.radius.db.connection import db
    return db()


def _set(key, value):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, key, str(value))


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status=422):
    body = res.get_json()
    assert res.status_code == status, body
    return body["error"]


def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_lim", "password": "owner-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _flashes(client):
    with client.session_transaction() as sess:
        return [m for _c, m in sess.get("_flashes", [])]


def _plan(price=30.0, **cols) -> int:
    now = datetime.utcnow().isoformat()
    fields = {"tenant_id": 1, "name": "l_" + uuid4().hex[:6], "duration_minutes": 30 * 1440,
              "price": price, "currency": "ILS", "speed_down_kbps": 1024,
              "speed_up_kbps": 512, "enabled": 1, "created_at": now, "updated_at": now}
    fields.update(cols)
    cur = _db().execute(
        f"INSERT INTO access_plans({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
        tuple(fields.values()))
    return int(cur.lastrowid)


def _sub(*, plan_id=None, balance=0.0, days=10):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = "l_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret",
        plan_id=plan_id or _plan(), full_name="Lim", mobile="0599000000", status="enabled",
        expire_at=datetime.utcnow() + timedelta(days=days)))
    _db().execute("UPDATE subscribers SET balance=? WHERE username=?", (balance, username))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────── defaults + exposure ───────────────

def test_defaults_equal_the_old_hardcoded_values_and_are_exposed(client):
    system = _data(client.get("/api/v1/settings", headers=AUTH))["system"]
    lim = system["limits"]
    assert lim["max_extend_days"] == 365 and lim["max_extend_minutes"] == 525600
    assert lim["max_subscriber_payment"] == 100000
    assert lim["max_subscriber_balance_add"] == 100000
    assert lim["max_distributor_balance_add"] == 100000
    assert lim["max_loan_amount"] == 100000 and lim["max_amount_generic"] == 100000
    assert lim["max_expiry_year"] == 2100 and lim["max_cards_per_batch"] == 10000
    assert lim["max_extend_days_unlimited"] is False
    from app.radius.core import limits
    assert limits.extend_too_long_msg() == OWNER_PHRASE     # 365 ⇒ «سنة» verbatim
    s = _sub()
    err = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                           json={"minutes": 525601}))
    assert OWNER_PHRASE in err["message"]


# ─────────────── max_extend_days ───────────────

def test_max_extend_days_everywhere_api_and_web(client):
    _set("limits.max_extend_days", 30)
    msg30 = "أقصى تمديد في المرة الواحدة 30 يومًا"
    s = _sub(balance=1000)
    # API extend
    assert msg30 in _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                                     json={"minutes": 30 * 1440 + 1}))["message"]
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                      json={"minutes": 30 * 1440}))
    # set-expiry jump (PATCH)
    far = datetime.utcnow() + timedelta(days=100)
    assert msg30 in _err(client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                                      json={"expire_at": _z(far)}))["message"]
    # bulk tool
    assert msg30 in _err(client.post("/api/v1/tools/general-adjustments", headers=AUTH,
                                     json={"action": "extend", "minutes": 31 * 1440,
                                           "usernames": [s.username], "dry_run": True})
                         )["message"]
    # loans centre (debt loan of 31 days)
    assert msg30 in _err(client.post("/api/v1/loans", headers=AUTH,
                                     json={"username": s.username, "days": 31,
                                           "price_from_days": True}))["message"]
    # change-plan compensation over 30 days
    dear, cheap = _plan(price=100.0), _plan(price=10.0)
    s2 = _sub(plan_id=dear, days=10)
    assert msg30 in _err(client.post(f"/api/v1/accounts/{s2.username}/change-plan",
                                     headers=AUTH, json={"plan_id": cheap,
                                                         "policy": "lower_compensate"})
                         )["message"]
    # web extend
    csrf = _web_login(client)
    r = client.post(f"/admin/radius/users/{s.username}/extend", headers=FETCH, data={
        "_csrf_token": csrf, "minutes": str(31 * 1440), "charge_mode": "free"})
    assert r.status_code == 422 and msg30 in r.get_json()["error"]
    # the bulk tool page shows the configured cap
    html = client.get("/admin/radius/tools/general_adjustments").get_data(as_text=True)
    assert 'max="43200"' in html


def test_unlimited_toggle_keeps_the_2100_ceiling(client):
    _set("limits.max_extend_days", 30)
    _set("limits.max_extend_days.unlimited", 1)
    s = _sub()
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                      json={"minutes": 400 * 1440}))                    # > 30 d: allowed
    from app.radius.core import limits
    assert limits.max_extend_days() == limits.TECH_MAX_EXTEND_DAYS
    s2 = _sub()
    _db().execute("UPDATE subscribers SET expire_at=? WHERE username=?",
                  (datetime(2100, 6, 1).isoformat(), s2.username))
    err = _err(client.post(f"/api/v1/accounts/{s2.username}/extend", headers=AUTH,
                           json={"minutes": 300 * 1440}))
    assert "المدة الناتجة تتجاوز الحدّ المسموح" in err["message"] and "2100" in err["message"]


# ─────────────── create is bound too (owner decision 1) ───────────────

def test_create_is_bound_by_max_extend_days_api_and_web(client):
    pid = _plan()
    far = datetime.utcnow() + timedelta(days=400)
    err = _err(client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "c_" + uuid4().hex[:6], "password": "secret1", "plan_id": pid,
        "expire_at": _z(far)}))
    assert "عند إنشاء المشترك سنة" in err["message"]
    _data(client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "c_" + uuid4().hex[:6], "password": "secret1", "plan_id": pid,
        "expire_at": _z(datetime.utcnow() + timedelta(days=300))}), 201)
    _set("limits.max_extend_days", 7)
    err = _err(client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "c_" + uuid4().hex[:6], "password": "secret1", "plan_id": pid,
        "expire_at": _z(datetime.utcnow() + timedelta(days=8))}))
    assert "7 أيام" in err["message"]
    # web create
    csrf = _web_login(client)
    local = datetime.utcnow() + timedelta(days=20)
    name = "cw_" + uuid4().hex[:6]
    r = client.post("/admin/radius/users", data={
        "_csrf_token": csrf, "username": name, "password": "pass1", "plan_id": str(pid),
        "service_type": "hotspot", "status": "enabled", "expire_year": str(local.year),
        "expire_month": str(local.month), "expire_day": str(local.day)})
    assert _get(name) is None
    assert r.status_code == 422 and "7 أيام" in r.get_data(as_text=True)


def test_migration_paths_are_not_bound(app):
    """MikroTik import / migration wizard copy accounts via the repo, not the
    service — an account expiring in 2090 is kept."""
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username="migrated_x", password="p", plan_id=_plan(),
        status="enabled", expire_at=datetime(2090, 1, 1)))
    assert _get("migrated_x").expire_at == datetime(2090, 1, 1)


# ─────────────── money limits ───────────────

def test_each_money_limit_is_enforced_with_its_value(client):
    _set("limits.max_subscriber_payment", 50)
    _set("limits.max_subscriber_balance_add", 20)
    _set("limits.max_loan_amount", 10)
    _set("limits.max_amount_generic", 70)
    s = _sub(balance=0)
    e = _err(client.post("/api/v1/payments", headers=AUTH,
                         json={"username": s.username, "amount": 50.01}))
    assert "(50)" in e["message"]
    _data(client.post("/api/v1/payments", headers=AUTH,
                      json={"username": s.username, "amount": 50}), 201)
    e = _err(client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH,
                         json={"amount": 20.01}))
    assert "(20)" in e["message"]
    e = _err(client.post("/api/v1/loans", headers=AUTH,
                         json={"username": s.username, "days": 1, "amount": 10.01}))
    assert "(10)" in e["message"]
    e = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                         json={"minutes": 60, "charge_mode": "debt", "amount": 50.01}))
    assert "(50)" in e["message"]
    e = _err(client.post("/api/v1/vouchers", headers=AUTH, json={"amount": 70.01}))
    assert "(70)" in e["message"]
    # web payment path (same service) and web vouchers
    csrf = _web_login(client)
    r = client.post(f"/admin/radius/users/{s.username}/extend", headers=FETCH, data={
        "_csrf_token": csrf, "minutes": "60", "charge_mode": "debt", "amount": "50.01"})
    assert r.status_code == 422 and "(50)" in r.get_json()["error"]
    client.post("/admin/radius/vouchers/generate", data={
        "_csrf_token": csrf, "count": "1", "amount": "70.01"})
    assert any("(70)" in m for m in _flashes(client))


def test_distributor_limit(app):
    _set("limits.max_distributor_balance_add", 40)
    from app.radius.core.errors import RadiusValidationError
    from app.radius.services.operations import get_operations_service
    ops = get_operations_service()
    d = ops.create_distributor(tenant_id=1, actor="admin",
                               data={"name": "dl_" + uuid4().hex[:4], "credit_limit": 1000.0})
    with pytest.raises(RadiusValidationError) as ei:
        ops.settle_distributor(tenant_id=1, distributor_id=int(d["id"]), actor="a",
                               data={"amount": 40.01, "direction": "credit",
                                     "apply_to": "balance"})
    assert "(40)" in ei.value.message
    ops.settle_distributor(tenant_id=1, distributor_id=int(d["id"]), actor="a",
                           data={"amount": 40, "direction": "credit", "apply_to": "balance"})


def test_card_batch_limit(client):
    _set("limits.max_cards_per_batch", 5)
    e = _err(client.post("/api/v1/cards/generate", headers=AUTH,
                         json={"plan_id": _plan(), "count": 6}))
    assert "5 بطاقة" in e["message"]


def test_max_expiry_year(client):
    _set("limits.max_extend_days", 36500)
    _set("limits.max_expiry_year", 2030)
    e = _err(client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "y_" + uuid4().hex[:6], "password": "secret1", "plan_id": _plan(),
        "expire_at": "2031-02-01T00:00:00Z"}))
    assert "2030" in e["message"]
    s = _sub()
    e = _err(client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                          json={"expire_at": "2031-01-01T00:00:00Z"}))
    assert "2030" in e["message"]
    _data(client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"expire_at": "2030-12-31T00:00:00Z"}))


# ─────────────── settings validation (API + web) ───────────────

@pytest.mark.parametrize("key,value", [
    ("limits.max_extend_days", "0"), ("limits.max_extend_days", ""),
    ("limits.max_extend_days", "abc"), ("limits.max_extend_days", "1.5"),
    ("limits.max_subscriber_payment", "-5"), ("limits.max_subscriber_payment", "1e308"),
    ("limits.max_expiry_year", "2101"), ("limits.max_expiry_year", "2020"),
    ("limits.max_cards_per_batch", "100001"),
])
def test_bad_limit_values_are_refused_api(client, key, value):
    e = _err(client.patch("/api/v1/settings", headers=AUTH, json={key: value}))
    assert e["details"]["field"] == key


def test_limit_settings_api_roundtrip_and_arabic_digits(client):
    _data(client.patch("/api/v1/settings", headers=AUTH,
                       json={"limits.max_extend_days": "٦٠", "limits.max_loan_amount": "2500.5"}))
    lim = _data(client.get("/api/v1/settings", headers=AUTH))["system"]["limits"]
    assert lim["max_extend_days"] == 60 and lim["max_loan_amount"] == 2500.5


def test_web_limits_section_saves_validates_and_toggles(client):
    csrf = _web_login(client)
    html = client.get("/admin/radius/settings").get_data(as_text=True)
    assert 'data-st-panel="limits"' in html and "limits.max_extend_days" in html
    client.post("/admin/radius/settings", data={
        "_csrf_token": csrf, "limits.max_extend_days": "0"})
    assert any("بلا حدّ" in m for m in _flashes(client))
    from app.radius.core import limits
    assert limits.max_extend_days(1) == 365                       # unchanged
    client.post("/admin/radius/settings", data={
        "_csrf_token": csrf, "limits.max_extend_days": "90",
        "limits.max_extend_days.unlimited": ["0"]})
    assert limits.max_extend_days(1) == 90
    client.post("/admin/radius/settings", data={
        "_csrf_token": csrf, "limits.max_extend_days": "90",
        "limits.max_extend_days.unlimited": ["1", "0"]})           # toggle on
    assert limits.is_unlimited("limits.max_extend_days", 1)
