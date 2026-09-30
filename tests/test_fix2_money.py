"""Fix wave 2 — money stream (R02/R03/R12 + money parts of R10/R11), 2026-09-29.

One test (or more) per item of the brief, API and web where the web shares the
path: price_from_days gates, void permissions, void semantics (ledger + payment),
caps (100,000 / one extend ≤ 1 year / no expiry after 2100, never 500), web
extend 0/empty, the shared Arabic translation layer, one pricing function,
idempotency (distributor settle, reset-daily, body fingerprint), debt display,
loans outstanding, small validation items and atomic enable/disable.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fix2-money-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
FETCH = {"X-Requested-With": "fetch"}
EXPIRE = datetime(2030, 1, 1, 12, 0, 0)
ONE_YEAR_AR = "أقصى تمديد في المرة الواحدة سنة"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fix2money.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fix2-money-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_f2")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()

    # The fixture keeps ONE app context open, so Flask reuses it (and ``g``) for
    # every test request: the first request's token identity (g._api_authed…)
    # would leak into the next one. Production has one app context per request;
    # mimic it by clearing the per-request ``g`` state first.
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


# ─────────────── helpers ───────────────

def _web_login(client, username="owner_f2", password="owner-pass") -> str:
    res = client.post("/admin/radius/login", data={"username": username, "password": password})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _flashes(client) -> list:
    with client.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _db():
    from app.radius.db.connection import db
    return db()


def _plan(name=None, *, price=30.0, days=30, minutes=None, currency="ILS", quota_mb=0) -> int:
    now = datetime.utcnow().isoformat()
    dur = days * 1440 if minutes is None else minutes
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, quota_total_mb, enabled, created_at, updated_at) "
        "VALUES(1,?,?,?,?,?,?,1,?,?)",
        (name or "p_" + uuid4().hex[:6], dur, days if minutes is None else 0, price,
         currency, quota_mb, now, now))
    return int(cur.lastrowid)


def _sub(username=None, *, plan_id, balance=0.0, expire_at=EXPIRE, status="enabled"):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "u_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Money User", mobile="0599000000", status=status, expire_at=expire_at))
    _db().execute("UPDATE subscribers SET balance=?, expire_at=? WHERE tenant_id=1 AND username=?",
                  (float(balance), expire_at.isoformat() if expire_at else None, username))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _loan_row(loan_id):
    from app.radius.db.repos import accounting_repo
    return accounting_repo.get_loan(1, loan_id)


def _count(sql, *args) -> int:
    return int(_db().execute(sql, args).fetchone()[0] or 0)


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, body)
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status, code=None):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, body)
    assert body["ok"] is False, body
    if code:
        assert body["error"]["code"] == code, body
    return body["error"]


def _manager(perms, *, password="mgr-pass"):
    from app.radius.db.repos import admins_repo
    role = admins_repo.create_role(name="r_" + uuid4().hex[:6], permissions=tuple(perms))
    return admins_repo.create_admin(username="m_" + uuid4().hex[:8], password=password,
                                    full_name="Manager", role_id=role.id,
                                    is_super_admin=False)


def _token_for(client, admin, password="mgr-pass") -> dict:
    res = client.post("/api/admin/login", json={"username": admin.username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": "Bearer " + res.get_json()["data"]["token"]}


def _approval_threshold(mgr_id, amount):
    from app.radius.services.business_os_finance import money_to_minor
    _db().execute(
        "INSERT INTO manager_distributor_policies(tenant_id, entity_type, entity_id, "
        "require_approval_above_minor, created_at) VALUES(1,'manager',?,?,?)",
        (mgr_id, money_to_minor(amount), datetime.utcnow().isoformat()))


def _pay(username, amount, **extra):
    return _data(__import__("flask").current_app.test_client().post(
        f"/api/v1/accounts/{username}/payment", headers=AUTH,
        json=dict({"amount": amount}, **extra)), 201)


# ═══════════ 1. price_from_days + amount 0 must pass the gates with the REAL amount ═══════════

def test_api_price_from_days_zero_amount_goes_to_the_approval_queue(client, monkeypatch):
    from app.radius.services import manager_grants
    monkeypatch.setattr(manager_grants, "action_permitted", lambda *a, **k: True)
    pid = _plan(price=50.0, days=30)
    s = _sub(plan_id=pid)
    mgr = _manager(("users.view", "users.loans"))
    hdr = _token_for(client, mgr)
    _approval_threshold(mgr.id, 10)
    d = _data(client.post("/api/v1/loans", headers=hdr, json={
        "username": s.username, "days": 30, "price_from_days": True, "amount": 0,
        "apply_to_radius": True, "dry_run": False}), 202)
    assert d["pending_approval"] is True and d["loan"] is None
    assert _count("SELECT COUNT(*) FROM loan_entries WHERE username=?", s.username) == 0
    queued = _db().execute("SELECT amount_minor FROM manager_pending_approvals "
                           "WHERE admin_id=?", (mgr.id,)).fetchone()
    assert queued is not None and int(queued[0]) == 5000   # the real 50.00, not 0
    assert _get(s.username).expire_at == EXPIRE


def test_api_price_from_days_zero_amount_hits_the_spend_gate(client, monkeypatch):
    from app.radius.services import manager_grants
    monkeypatch.setattr(manager_grants, "action_permitted", lambda *a, **k: True)
    pid = _plan(price=50.0, days=30)
    s = _sub(plan_id=pid)
    mgr = _manager(("users.view", "users.loans"))
    hdr = _token_for(client, mgr)
    # Zero-trust manager (no wallet): a 50.00 debt loan must be blocked even
    # though the client posted "amount": 0.
    _err(client.post("/api/v1/loans", headers=hdr, json={
        "username": s.username, "days": 30, "price_from_days": True, "amount": 0,
        "apply_to_radius": True}), 403, "spend_blocked")
    assert _count("SELECT COUNT(*) FROM loan_entries WHERE username=?", s.username) == 0


def test_web_price_from_days_goes_to_the_approval_queue(client, monkeypatch):
    from app.radius.services import manager_grants
    monkeypatch.setattr(manager_grants, "action_permitted", lambda *a, **k: True)
    pid = _plan(price=420.0, days=30)
    s = _sub(plan_id=pid)
    mgr = _manager(("users.view", "users.loans"))
    # D09 (fix wave 2): the web reaches only the manager's OWN subscribers.
    from app.radius.db.connection import db
    db().execute("UPDATE subscribers SET manager_id=? WHERE username=?", (mgr.id, s.username))
    _approval_threshold(mgr.id, 10)
    csrf = _web_login(client, mgr.username, "mgr-pass")
    res = client.post(f"/admin/radius/users/{s.username}/loans", headers=FETCH, data={
        "_csrf_token": csrf, "days": "30", "price_from_days": "1", "apply_to_radius": "1"})
    body = res.get_json()
    assert body["ok"] is True and body.get("pending_approval") is True, body
    assert _count("SELECT COUNT(*) FROM loan_entries WHERE username=?", s.username) == 0
    assert _get(s.username).expire_at == EXPIRE


# ═══════════ 2. void endpoints = the web's owner-only rule ═══════════

def test_api_void_endpoints_need_the_owner(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    d = _pay(s.username, 30)
    mgr = _manager(("dashboard.view", "users.view", "users.payments"))
    hdr = _token_for(client, mgr)
    _err(client.post("/api/v1/ledger/void", headers=hdr,
                     json={"entry_id": d["payment"]["ledger_entry_id"]}), 403, "forbidden")
    _err(client.post(f"/api/v1/payments/{d['payment']['id']}/void", headers=hdr, json={}),
         403, "forbidden")
    assert _get(s.username).expire_at == EXPIRE + timedelta(days=30)   # untouched
    _data(client.post(f"/api/v1/payments/{d['payment']['id']}/void", headers=AUTH, json={}), 201)
    assert _get(s.username).expire_at == EXPIRE


# ═══════════ 3. ledger void reverses the effect ═══════════

def test_void_cash_balance_entry_restores_the_balance(client):
    pid = _plan()
    s = _sub(plan_id=pid, balance=2)
    _data(client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH, json={"amount": 10}))
    assert float(_get(s.username).balance) == 12.0
    eid = _db().execute("SELECT id FROM accounting_ledger_entries WHERE username=? "
                        "AND entry_type='cash_balance'", (s.username,)).fetchone()[0]
    v = _data(client.post("/api/v1/ledger/void", headers=AUTH, json={"entry_id": eid}), 201)
    assert v["entry"]["wallet_delta"] == -10.0
    assert float(_get(s.username).balance) == 2.0
    _err(client.post("/api/v1/ledger/void", headers=AUTH, json={"entry_id": eid}), 409)
    assert float(_get(s.username).balance) == 2.0


def test_void_debt_extension_entry_restores_balance_and_takes_time_back(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH, json={
        "minutes": 1440, "charge_mode": "debt"}))
    assert float(_get(s.username).balance) == -1.0
    assert _get(s.username).expire_at == EXPIRE + timedelta(days=1)
    eid = _db().execute("SELECT id FROM accounting_ledger_entries WHERE username=? "
                        "AND entry_type='debt'", (s.username,)).fetchone()[0]
    _data(client.post("/api/v1/ledger/void", headers=AUTH, json={"entry_id": eid}), 201)
    assert float(_get(s.username).balance) == 0.0
    assert _get(s.username).expire_at == EXPIRE


def test_void_loan_entry_closes_the_loan_and_takes_its_time_back(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 2}), 201)
    loan = d["loan"]
    assert _get(s.username).expire_at == EXPIRE + timedelta(days=2)
    _data(client.post("/api/v1/ledger/void", headers=AUTH,
                      json={"entry_id": loan["ledger_entry_id"]}), 201)
    assert _loan_row(loan["id"])["status"] == "voided"
    assert _get(s.username).expire_at == EXPIRE
    # Nothing left owed.
    assert _data(client.get(f"/api/v1/accounts/{s.username}/actions-context",
                            headers=AUTH))["open_loans"] == []


def test_void_loan_with_settlements_is_409_and_settlement_void_reopens(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    loan = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 4}), 201)["loan"]          # 4.00
    st = _data(client.post(f"/api/v1/loans/{loan['id']}/settle", headers=AUTH,
                           json={"amount": 4}), 201)["settlement"]
    assert _loan_row(loan["id"])["status"] == "settled"
    err = _err(client.post("/api/v1/ledger/void", headers=AUTH,
                           json={"entry_id": loan["ledger_entry_id"]}), 409)
    assert "تسوي" in err["message"]
    _data(client.post("/api/v1/ledger/void", headers=AUTH,
                      json={"entry_id": st["ledger_entry_id"]}), 201)
    row = _loan_row(loan["id"])
    assert row["status"] == "open" and row["outstanding"] == 4.0
    # now the loan itself can be cancelled
    _data(client.post("/api/v1/ledger/void", headers=AUTH,
                      json={"entry_id": loan["ledger_entry_id"]}), 201)
    assert _loan_row(loan["id"])["status"] == "voided"


def test_void_writeoff_entry_reopens_the_loan(client):
    from app.radius.services.accounting import AccountingService
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    loan = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 3}), 201)["loan"]
    out = AccountingService(1).writeoff_loan(loan["id"], actor="t")
    _data(client.post("/api/v1/ledger/void", headers=AUTH,
                      json={"entry_id": out["writeoff_ledger_entry_id"]}), 201)
    row = _loan_row(loan["id"])
    assert row["status"] == "open" and row["outstanding"] == 3.0


def test_web_ledger_void_of_cash_balance_restores_balance(client):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid, balance=0)
    _data(client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH, json={"amount": 7}))
    eid = _db().execute("SELECT id FROM accounting_ledger_entries WHERE username=? "
                        "AND entry_type='cash_balance'", (s.username,)).fetchone()[0]
    client.post("/admin/radius/finance/ledger/void", data={"_csrf_token": csrf, "entry_id": str(eid)})
    assert float(_get(s.username).balance) == 0.0


# ═══════════ 4. voiding a payment ═══════════

def test_void_payment_on_formerly_unlimited_subscriber_keeps_it_unlimited(client):
    pid = _plan(price=50.0, days=30)
    s = _sub(plan_id=pid, expire_at=None)
    d = _pay(s.username, 50)
    assert _get(s.username).expire_at is not None       # documented: payment activates
    v = _data(client.post(f"/api/v1/payments/{d['payment']['id']}/void", headers=AUTH,
                          json={}), 201)
    assert v["time_reversal"]["status"] == "restored_unlimited"
    assert _get(s.username).expire_at is None             # was: expire = now → locked out


def test_void_payment_reverses_the_loan_settlements_it_made(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    loan = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 30}), 201)["loan"]        # 30.00
    d = _pay(s.username, 20, loan_actions=[{"loan_id": loan["id"], "action": "settle"}])
    assert _loan_row(loan["id"])["outstanding"] == 10.0
    v = _data(client.post(f"/api/v1/payments/{d['payment']['id']}/void", headers=AUTH,
                          json={}), 201)
    assert v["settlements_reversed"]["loans"][0]["loan_id"] == loan["id"]
    row = _loan_row(loan["id"])
    assert row["status"] == "open" and row["outstanding"] == 30.0
    # A settlement that closed the loan reopens it too.
    d2 = _pay(s.username, 30, loan_actions=[{"loan_id": loan["id"], "action": "settle"}])
    assert _loan_row(loan["id"])["status"] == "settled"
    _data(client.post(f"/api/v1/payments/{d2['payment']['id']}/void", headers=AUTH, json={}), 201)
    assert _loan_row(loan["id"])["status"] == "open"
    assert _loan_row(loan["id"])["outstanding"] == 30.0


def test_void_payment_restores_the_balance_debt_it_settled(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid, balance=-10)
    d = _pay(s.username, 30, settle_balance=True)
    assert float(_get(s.username).balance) == 0.0
    v = _data(client.post(f"/api/v1/payments/{d['payment']['id']}/void", headers=AUTH,
                          json={}), 201)
    assert v["settlements_reversed"]["debt_restored"] == 10.0
    assert float(_get(s.username).balance) == -10.0


# ═══════════ 5. caps (never 500) ═══════════

def test_payment_caps_are_422_never_500(client):
    pid = _plan(price=50.0, days=30)
    s = _sub(plan_id=pid)
    for amount in (1_000_000, 5_000_000, 999_999_999, 1e9):
        _err(client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH,
                         json={"amount": amount}), 422)
        _err(client.post("/api/v1/payments", headers=AUTH,
                         json={"username": s.username, "amount": amount,
                               "apply_to_radius": True}), 422)
    # 400 ILS on 50/30 days = 240 days → fine; 700 = 420 days → more than a year.
    err = _err(client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH,
                           json={"amount": 700}), 422)
    assert ONE_YEAR_AR in err["message"]
    assert _count("SELECT COUNT(*) FROM payment_transactions WHERE username=?", s.username) == 0
    assert _get(s.username).expire_at == EXPIRE
    _pay(s.username, 400)
    # Money not converted to time keeps the 100,000 cap.
    _err(client.post("/api/v1/payments", headers=AUTH,
                     json={"username": s.username, "amount": 100_000.01}), 422)


def test_extend_caps_one_year_per_operation_and_2100(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    err = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                           json={"minutes": 366 * 1440}), 422)
    assert ONE_YEAR_AR in err["message"]
    err = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                           json={"minutes": 1000 * 1440}), 422)
    assert ONE_YEAR_AR in err["message"]
    # 999,999 days is beyond the numeric input range → 422 (Arabic), never 500
    _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                     json={"minutes": 999_999 * 1440}), 422)
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                      json={"minutes": 365 * 1440}))
    assert _get(s.username).expire_at == EXPIRE + timedelta(days=365)
    # repeating is allowed
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                      json={"minutes": 365 * 1440}))
    # set-expiry jump of more than a year → 422; beyond 2100 → 422 (never 500)
    far = (_get(s.username).expire_at + timedelta(days=400)).isoformat()
    err = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                           json={"expire_at": far}), 422)
    assert ONE_YEAR_AR in err["message"]
    err = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                           json={"expire_at": "2150-01-01T00:00:00"}), 422)
    assert "الحدّ المسموح" in err["message"]
    for bad in ("9999-12-31T23:59:59-05:00", "0001-01-01T00:00:00+03:00"):
        _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                         json={"expire_at": bad}), 422)
    # an expiry already at 9999-12-31 + 1 minute → 422, not 500
    t = _sub(plan_id=pid, expire_at=datetime(9999, 12, 31, 23, 0, 0))
    _err(client.post(f"/api/v1/accounts/{t.username}/extend", headers=AUTH,
                     json={"minutes": 1}), 422)
    _err(client.post(f"/api/v1/accounts/{t.username}/extend_time", headers=AUTH,
                     json={"minutes": 1}), 422)
    # amount cap on a paid/debt extend
    _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                     json={"minutes": 60, "charge_mode": "debt", "amount": 100_000.01}), 422)


def test_loan_time_is_capped_at_one_year(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    err = _err(client.post("/api/v1/loans", headers=AUTH, json={
        "username": s.username, "days": 400, "price_from_days": True,
        "apply_to_radius": True}), 422)
    assert ONE_YEAR_AR in err["message"]
    assert _data(client.get(f"/api/v1/accounts/{s.username}/actions-context",
                            headers=AUTH))["max_debt_loan_days"] == 365


def test_web_extend_caps(client):
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid, balance=0)
    res = client.post(f"/admin/radius/users/{s.username}/extend", headers=FETCH, data={
        "_csrf_token": csrf, "minutes": str(999_999 * 1440), "charge_mode": "free"})
    assert res.status_code == 422 and ONE_YEAR_AR in res.get_json()["error"]
    res = client.post(f"/admin/radius/users/{s.username}/extend", headers=FETCH, data={
        "_csrf_token": csrf, "minutes": "60", "charge_mode": "debt", "amount": "1e11"})
    assert res.status_code == 422
    res = client.post(f"/admin/radius/users/{s.username}/extend", data={
        "_csrf_token": csrf, "minutes": "60", "charge_mode": "debt", "amount": "2000000000"})
    assert res.status_code in {302, 303}
    after = _get(s.username)
    assert after.expire_at == EXPIRE and float(after.balance) == 0.0
    assert _count("SELECT COUNT(*) FROM accounting_ledger_entries WHERE username=?",
                  s.username) == 0
    # bulk: more than a year is refused before anything is written
    client.post("/admin/radius/users/extend-bulk", data={
        "_csrf_token": csrf, "usernames": s.username, "minutes": str(400 * 1440),
        "charge_mode": "free"})
    assert _get(s.username).expire_at == EXPIRE
    assert any(ONE_YEAR_AR in m for _c, m in _flashes(client))


def test_web_payment_over_one_year_is_refused_in_arabic(client):
    csrf = _web_login(client)
    pid = _plan(price=50.0, days=30)
    s = _sub(plan_id=pid)
    res = client.post(f"/admin/radius/users/{s.username}/payments", headers=FETCH, data={
        "_csrf_token": csrf, "amount": "5000000", "apply_to_radius": "1"})
    body = res.get_json()
    assert res.status_code == 422 and body["ok"] is False
    assert "date value out of range" not in body["error"]
    assert _count("SELECT COUNT(*) FROM payment_transactions WHERE username=?", s.username) == 0


# ═══════════ 6. web extend 0 / empty / cleared + Arabic messages ═══════════

@pytest.mark.parametrize("form", [
    {"minutes": "0"}, {"minutes": ""}, {"minutes": "-5"}, {"expire_at": ""},
])
def test_web_extend_zero_or_empty_is_refused_not_one_minute(client, form):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid)
    res = client.post(f"/admin/radius/users/{s.username}/extend", headers=FETCH,
                      data=dict({"_csrf_token": csrf, "charge_mode": "free"}, **form))
    assert res.status_code == 422
    msg = res.get_json()["error"]
    assert "مطلوب" in msg or "أكبر من صفر" in msg
    assert _get(s.username).expire_at == EXPIRE
    # plain form post: Arabic flash, nothing added
    client.post(f"/admin/radius/users/{s.username}/extend",
                data=dict({"_csrf_token": csrf, "charge_mode": "free"}, **form))
    assert _get(s.username).expire_at == EXPIRE


def test_web_flashes_are_arabic_never_raw_english(client):
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    nodur = _plan(price=12.0, days=0, minutes=0)
    s = _sub(plan_id=pid, balance=100)
    english = ("must be", "required", "unknown", "selected plan", "quota_mb", "duration_minutes")

    def _check(resp_or_none=None):
        msgs = [m for _c, m in _flashes(client)]
        if resp_or_none is not None and resp_or_none.is_json:
            msgs.append(resp_or_none.get_json().get("error") or "")
        for m in msgs:
            assert not any(e in m for e in english), m
        return msgs

    r = client.post(f"/admin/radius/users/{s.username}/extend", headers=FETCH, data={
        "_csrf_token": csrf, "minutes": "60", "charge_mode": "bogus"})
    assert "غير معروفة" in _check(r)[-1]
    r = client.post(f"/admin/radius/users/{s.username}/extend", headers=FETCH, data={
        "_csrf_token": csrf, "minutes": "60", "charge_mode": "debt", "amount": "-5"})
    assert "أكبر من صفر" in _check(r)[-1]
    client.post(f"/admin/radius/users/{s.username}/change-plan", data={
        "_csrf_token": csrf, "plan_id": str(nodur), "policy": "higher_debt"})
    _check()
    r = client.post(f"/admin/radius/users/{s.username}/loans", headers=FETCH, data={
        "_csrf_token": csrf, "days": "0", "hours": "0"})
    assert "حدّد مدّة السلفة" in r.get_json()["error"]
    client.post(f"/admin/radius/users/{s.username}/quota/topup", data={
        "_csrf_token": csrf, "quota_mb": "0", "charge_mode": "free"})
    _check()


def test_shared_translation_layer():
    from app.radius.core.messages_ar import translate_service_message as t
    assert t("amount must be > 0") == "المبلغ يجب أن يكون أكبر من صفر."
    assert t("minutes > 0 required") == "المدّة يجب أن تكون أكبر من صفر."
    assert t("unknown extend charge mode") == "طريقة الإضافة غير معروفة."
    assert "المبلغ" in t("amount must be >= 0.01") and "0.01" in t("amount must be >= 0.01")
    assert "عددًا صحيحًا" in t("duration_minutes must be an integer")
    assert "رقمًا" in t("custom_price must be a number")
    assert t("رسالة عربية") == "رسالة عربية"
    # the API table is the same object (one layer)
    from app.api.v1 import subscriber_actions
    from app.radius.core import messages_ar
    assert subscriber_actions._SERVICE_MSG_AR is messages_ar.SERVICE_MSG_AR


def test_service_layer_raises_no_english_validation_messages():
    import re
    from pathlib import Path
    pat = re.compile(r"Radius(Validation|Conflict|NotFound)Error\(\s*f?[\"'][A-Za-z]")
    for rel in ("app/radius/services/users.py", "app/radius/services/accounting.py",
                "app/radius/services/radius_apply.py"):
        src = Path(rel).read_text(encoding="utf-8")
        assert not pat.search(src), rel


def test_api_payment_messages_are_arabic(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    for body in ({"amount": 0.001}, {"amount": 5, "rounding_mode": "x"},
                 {"amount": 5, "discount_amount": -1}, {"amount": 5, "custom_price": "abc"}):
        err = _err(client.post("/api/v1/payments", headers=AUTH,
                               json=dict({"username": s.username}, **body)), 422)
        assert not any(w in err["message"] for w in ("must", "should", "nearest or")), err


# ═══════════ 7. one pricing function / rounding / the lost minute ═══════════

def test_pricing_is_half_up_everywhere_and_minutes_are_exact(client):
    from app.radius.services.accounting import (
        calculate_proportional_amount as amt, calculate_proportional_minutes as mins)
    assert amt(minutes=180, plan_price=5, base_minutes=1440) == 0.63
    assert amt(minutes=3, plan_price=2.5, base_minutes=60) == 0.13
    assert amt(minutes=27, plan_price=2.5, base_minutes=60) == 1.13
    assert mins(amount_paid=7, plan_price=5, base_minutes=1440) == 2016
    assert mins(amount_paid=17.5, plan_price=50, base_minutes=43200) == 15120
    assert mins(amount_paid=8.75, plan_price=50, base_minutes=43200) == 7560
    pid = _plan(price=5.0, days=1)
    s = _sub(plan_id=pid, balance=10)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH, json={
        "minutes": 180, "charge_mode": "debt"}))
    assert d["charged_amount"] == 0.63
    t = _sub(plan_id=pid)
    d = _pay(t.username, 7)
    assert d["added_minutes"] == 2016
    from app.radius.core.numbers import round_money
    assert round_money(376.905) == 376.91 and round_money(184.615) == 184.62


def test_web_bulk_extend_charges_the_same_rounded_price(client):
    csrf = _web_login(client)
    pid = _plan(price=5.0, days=1)
    s = _sub(plan_id=pid)
    client.post("/admin/radius/users/extend-bulk", data={
        "_csrf_token": csrf, "usernames": s.username, "minutes": "180", "charge_mode": "debt"})
    assert float(_get(s.username).balance) == -0.63      # was −0.62 (bulk) vs 0.63 (dialog)


def test_web_pricing_js_uses_the_integer_half_up_formula(client):
    csrf = _web_login(client)
    pid = _plan()
    _sub(plan_id=pid)
    html = client.get("/admin/radius/users").get_data(as_text=True)
    assert "function hrPriceOfMinutes" in html
    assert "Math.round(price * (minutes / planMin) * 100)" not in html
    assert "Math.max(1, readDurationMinutes(form))" not in html


# ═══════════ 8. idempotency ═══════════

def test_distributor_settle_idempotency_api(client):
    d = _data(client.post("/api/v1/distributors", headers=AUTH,
                          json={"name": "d_" + uuid4().hex[:6]}), 201)
    did = d["distributor"]["id"]
    hdr = dict(AUTH, **{"Idempotency-Key": "dist-" + uuid4().hex})
    for _ in range(3):
        r = client.post(f"/api/v1/distributors/{did}/settle", headers=hdr,
                        json={"amount": 1, "direction": "debit"})
        assert r.status_code == 201
    debt = _db().execute("SELECT debt_balance FROM distributors WHERE id=?", (did,)).fetchone()[0]
    assert float(debt) == 1.0
    _err(client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                     json={"amount": 1_000_000_000, "direction": "credit"}), 422)


def test_distributor_settle_idempotency_web(client):
    csrf = _web_login(client)
    d = _data(client.post("/api/v1/distributors", headers=AUTH,
                          json={"name": "d_" + uuid4().hex[:6]}), 201)
    did = d["distributor"]["id"]
    page = client.get(f"/admin/radius/distributors/{did}").get_data(as_text=True)
    assert 'name="client_request_id"' in page
    key = uuid4().hex
    for _ in range(3):
        client.post(f"/admin/radius/distributors/{did}/settle", data={
            "_csrf_token": csrf, "client_request_id": key, "amount": "1",
            "direction": "debit"})
    debt = _db().execute("SELECT debt_balance FROM distributors WHERE id=?", (did,)).fetchone()[0]
    assert float(debt) == 1.0


def test_quota_reset_daily_is_idempotent(client):
    pid = _plan()
    s = _sub(plan_id=pid, balance=10)
    hdr = dict(AUTH, **{"Idempotency-Key": "qr-" + uuid4().hex})
    for _ in range(2):
        r = client.post(f"/api/v1/accounts/{s.username}/quota/reset-daily", headers=hdr,
                        json={"charge_mode": "paid", "amount": 2})
        assert r.status_code == 200, r.get_json()
    assert float(_get(s.username).balance) == 8.0


def test_same_key_with_another_body_or_subscriber_is_422(client):
    pid = _plan()
    a, b = _sub(plan_id=pid), _sub(plan_id=pid)
    key = "k-" + uuid4().hex
    hdr = dict(AUTH, **{"Idempotency-Key": key})
    _data(client.post("/api/v1/payments", headers=hdr,
                      json={"username": a.username, "amount": 5}), 201)
    err = _err(client.post("/api/v1/payments", headers=hdr,
                           json={"username": b.username, "amount": 5}), 422)
    assert err["message"].startswith("مفتاح التكرار استُخدم لطلب مختلف")
    _err(client.post("/api/v1/payments", headers=hdr,
                     json={"username": a.username, "amount": 6}), 422)
    assert _count("SELECT COUNT(*) FROM payment_transactions WHERE username=?", b.username) == 0
    # per-subscriber path: the same key on another subscriber's route → 422 too
    k2 = dict(AUTH, **{"Idempotency-Key": "k2-" + uuid4().hex})
    _data(client.post(f"/api/v1/accounts/{a.username}/extend", headers=k2,
                      json={"minutes": 60}))
    _err(client.post(f"/api/v1/accounts/{b.username}/extend", headers=k2,
                     json={"minutes": 60}), 422)
    assert _get(b.username).expire_at == EXPIRE
    # exact replay still works
    r = client.post(f"/api/v1/accounts/{a.username}/extend", headers=k2, json={"minutes": 60})
    assert r.status_code == 200 and r.headers.get("Idempotent-Replay") == "true"
    # a 5,000-char key is refused
    _err(client.post("/api/v1/payments", headers=dict(AUTH, **{"Idempotency-Key": "x" * 5000}),
                     json={"username": a.username, "amount": 5}), 422)


# ═══════════ 9. debt display ═══════════

def test_360_and_actions_context_show_the_balance_debt(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid, balance=-8045.26)
    _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 3}), 201)                   # +3.00 loan
    d = _data(client.get(f"/api/v1/accounts/{s.username}/360", headers=AUTH))
    assert d["overview"]["wallet_balance"] == -8045.26
    assert d["overview"]["open_debt"] == 8048.26
    assert d["financial"]["balance_debt"] == 8045.26
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["debt"] == 8045.26 and ctx["open_debt_total"] == 8048.26
    z = _sub(plan_id=pid, balance=0)
    raw = client.get(f"/api/v1/accounts/{z.username}/actions-context", headers=AUTH)
    assert '"debt":-0.0' not in raw.get_data(as_text=True).replace(" ", "")
    assert math.copysign(1, raw.get_json()["data"]["debt"]) == 1.0


def test_web_360_shows_the_open_debt(client):
    _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid, balance=-45.5)
    html = client.get(f"/admin/radius/users/{s.username}/360").get_data(as_text=True)
    assert "دين مفتوح" in html and "45.50" in html


# ═══════════ 10. loans outstanding ═══════════

def test_web_loans_center_and_finance_page_show_outstanding(client):
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    loan = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 5}), 201)["loan"]          # 5.00
    _data(client.post(f"/api/v1/loans/{loan['id']}/settle", headers=AUTH,
                      json={"amount": 2}), 201)                 # 3.00 left
    from app.radius.services.business_os_finance_center import FinanceCenterService
    debts = FinanceCenterService().debts(tenant_id=1)
    assert debts["total"] == "3.00"
    assert FinanceCenterService().dashboard(tenant_id=1)["total_loans"] == "3.00"
    api_total = _data(client.get("/api/v1/loans?status=open", headers=AUTH))["totals"]["outstanding"]
    assert float(debts["total"]) == api_total
    page = client.get(f"/admin/radius/users/{s.username}/finance").get_data(as_text=True)
    assert 'value="3.00"' in page                                # settle prefill = outstanding
    assert 'data-loan-amount="3.0"' in page
    # the prefilled value settles it (was «يتجاوز المتبقّي»)
    client.post(f"/admin/radius/users/{s.username}/loans/{loan['id']}/settle",
                data={"_csrf_token": csrf, "amount": "3.00"})
    assert _loan_row(loan["id"])["status"] == "settled"


def test_debt_loan_on_a_plan_without_duration_matches_the_preview(client):
    pid = _plan(price=12.0, days=0, minutes=0)
    s = _sub(plan_id=pid)
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    preview = round(ctx["effective_price"] * (30 * 1440) / ctx["plan"]["minutes"], 2)
    loan = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 30}), 201)["loan"]
    assert loan["amount"] == preview == 12.0                   # was 0.00
    d = _data(client.post("/api/v1/loans", headers=AUTH, json={
        "username": s.username, "days": 30, "price_from_days": True,
        "apply_to_radius": False}), 201)
    assert d["loan"]["amount"] == 12.0


# ═══════════ 11. small items ═══════════

def test_small_money_edge_values(client):
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    loan = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 5}), 201)["loan"]
    _err(client.post(f"/api/v1/loans/{loan['id']}/settle", headers=AUTH,
                     json={"amount": 0.004}), 422)
    _err(client.post(f"/api/v1/loans/{loan['id']}/settle", headers=AUTH,
                     json={"amount": True}), 422)
    assert _count("SELECT COUNT(*) FROM settlement_entries WHERE loan_id=?", loan["id"]) == 0
    # web: 0.004 on debt no longer buys a free hour with a 0.00 row
    before = _get(s.username)
    client.post(f"/admin/radius/users/{s.username}/extend", data={
        "_csrf_token": csrf, "minutes": "60", "charge_mode": "debt", "amount": "0.004"})
    after = _get(s.username)
    assert after.expire_at == before.expire_at
    assert _count("SELECT COUNT(*) FROM accounting_ledger_entries WHERE username=? "
                  "AND amount = 0", s.username) == 0


def test_payments_bad_bodies_are_422_not_500(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    for body in ([1], "x"):
        _err(client.post("/api/v1/payments", headers=AUTH, json=body), 422)
    _err(client.post("/api/v1/payments", headers=AUTH,
                     json={"username": s.username, "amount": 5, "plan_id": "abc"}), 422)
    r = client.post("/api/v1/payments", headers=dict(AUTH, **{"Content-Type": "application/json"}),
                    data="{not json")
    assert r.status_code == 422


def test_loans_read_needs_a_loans_or_finance_permission(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    loan = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "debt", "days": 1}), 201)["loan"]
    nol = _token_for(client, _manager(("dashboard.view", "users.view", "users.payments")))
    _err(client.get("/api/v1/loans", headers=nol), 403, "forbidden")
    _err(client.get(f"/api/v1/loans/{loan['id']}", headers=nol), 403, "forbidden")
    # fix3: the subscriber belongs to nobody — reading its loan needs the
    # «عرض كل المشتركين» scope (loans are scoped like every money list).
    ok_hdr = _token_for(client, _manager(("users.view", "users.loans",
                                          "scope.view_all_subscribers")))
    assert _data(client.get("/api/v1/loans", headers=ok_hdr))["count"] >= 1
    _data(client.get(f"/api/v1/loans/{loan['id']}", headers=ok_hdr))


def test_legacy_extend_time_rejects_fractions_and_booleans(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    for bad in (1.9, True, "abc"):
        _err(client.post(f"/api/v1/accounts/{s.username}/extend_time", headers=AUTH,
                         json={"minutes": bad}), 422)
    assert _get(s.username).expire_at == EXPIRE
    d = _data(client.post(f"/api/v1/accounts/{s.username}/extend_time", headers=AUTH,
                          json={"minutes": 2}))
    assert d["extended_minutes"] == 2


# ═══════════ 12. enable/disable in one BEGIN IMMEDIATE ═══════════

def test_enable_disable_run_inside_one_transaction(client, monkeypatch):
    from app.radius.db.connection import in_transaction
    from app.radius.services.users import get_users_service
    pid = _plan()
    s = _sub(plan_id=pid)
    svc = get_users_service()
    seen = []
    real_get = svc._adapter.get_account

    def _spy(username):
        seen.append(in_transaction())      # the READ must already hold the write lock
        return real_get(username)

    monkeypatch.setattr(svc._adapter, "get_account", _spy)
    svc.disable(actor="t", username=s.username)
    svc.enable(actor="t", username=s.username)
    assert seen and all(seen)
    assert _get(s.username).status == "enabled"
