"""Mobile-app subscriber actions ⇄ web panel parity (2026-09-28).

Every ``/api/v1/accounts/<u>/…`` action runs the SAME shared code as its web
route (services/subscriber_actions + the users/accounting services) and the
SAME permission decision (routes/blueprint.rbac_denial_status). The parity
tests below perform one action through the web route on subscriber A and
through the API on an identical subscriber B, then compare the resulting DB
state (subscriber row, ledger, payments, loans, settlements)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "actions-parity-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "actions.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "actions-secret")
    # The bootstrap admin (created on first boot) is the primary owner — the
    # only unrestricted principal. Give it known credentials for the web login.
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_parity")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass")
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
        owner = admins_repo.get_by_username("owner_parity")
        assert owner is not None and admins_repo.admin_is_owner(owner)
        application.config["TEST_OWNER"] = owner.username
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────── helpers ───────────────

def _web_login(client) -> str:
    """Log the web client in as the owner (primary admin) → CSRF token."""
    uname = client.application.config["TEST_OWNER"]
    res = client.post("/admin/radius/login", data={"username": uname, "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _manager(role_perms, *, password="mgr-pass"):
    """A regular (non-owner) manager with a custom role → (admin, api token)."""
    from app.radius.db.repos import admins_repo
    role = admins_repo.create_role(name="r_" + uuid4().hex[:6], permissions=tuple(role_perms))
    return admins_repo.create_admin(username="m_" + uuid4().hex[:8], password=password,
                                    full_name="Manager", role_id=role.id,
                                    is_super_admin=False)


def _api_token_for(client, admin, password="mgr-pass") -> dict:
    res = client.post("/api/admin/login", json={"username": admin.username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": "Bearer " + res.get_json()["data"]["token"]}


def _plan(name=None, *, price=30.0, days=30) -> int:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat()
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?)",
        (name or "p_" + uuid4().hex[:6], days * 1440, days, price, "ILS", now, now))
    return int(cur.lastrowid)


EXPIRE = datetime(2030, 1, 1, 12, 0, 0)


def _sub(username, *, plan_id, balance=0.0, mobile="0599000000", expire_at=EXPIRE):
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import db
    from app.radius.db.repos import subscribers_repo
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Parity User", mobile=mobile, status="enabled", expire_at=expire_at))
    db().execute("UPDATE subscribers SET balance=? WHERE tenant_id=1 AND username=?",
                 (float(balance), username))
    return subscribers_repo.get_subscriber(1, username)


def _pair(**kw):
    tag = uuid4().hex[:6]
    return _sub(f"wa{tag}", **kw), _sub(f"ap{tag}", **kw)


def _row(username):
    from app.radius.db.repos import subscribers_repo
    s = subscribers_repo.get_subscriber(1, username)
    return {"balance": round(float(s.balance or 0), 2), "expire_at": s.expire_at,
            "plan_id": s.plan_id, "combined_quota_mb": s.combined_quota_mb,
            "quota_limit_enabled": bool(s.quota_limit_enabled)}


def _rows(sql, username):
    from app.radius.db.connection import db
    return [dict(r) for r in db().execute(sql, (username,)).fetchall()]


def _state(username) -> dict:
    """Everything an action can touch, minus ids / names / timestamps / actor."""
    ledger = _rows("SELECT entry_type, direction, amount, currency, source_type "
                   "FROM accounting_ledger_entries WHERE tenant_id=1 AND username=? "
                   "ORDER BY id", username)
    payments = _rows("SELECT amount, currency, method, plan_price, effective_price, "
                     "earned_minutes, rounding_mode, status FROM payment_transactions "
                     "WHERE tenant_id=1 AND username=? ORDER BY id", username)
    loans = _rows("SELECT duration_minutes, amount, currency, status, reason "
                  "FROM loan_entries WHERE tenant_id=1 AND username=? ORDER BY id", username)
    settlements = _rows("SELECT s.amount, s.currency, s.method FROM settlement_entries s "
                        "JOIN loan_entries l ON l.id = s.loan_id "
                        "WHERE l.tenant_id=1 AND l.username=? ORDER BY s.id", username)
    return {"sub": _row(username), "ledger": ledger, "payments": payments,
            "loans": loans, "settlements": settlements}


def _open_loan(username, *, days=2, debt=True):
    from app.radius.services.accounting import AccountingService
    loan = AccountingService(1).create_loan({
        "username": username, "days": str(days), "hours": "0",
        "price_from_days": debt, "amount": 0, "currency": "ILS",
        "reason": "seed", "apply_to_radius": False}, actor="seed")
    return int(loan["id"])


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status, code):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is False and body["error"]["code"] == code, body
    return body["error"]


# ─────────────── actions-context ───────────────

def test_actions_context_full_shape(client):
    pid = _plan(price=30.0, days=30)
    s = _sub("ctx_" + uuid4().hex[:6], plan_id=pid, balance=-12.5)
    _open_loan(s.username, days=3)
    d = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert d["username"] == s.username and d["status"] == "enabled"
    assert d["expire_at"] == "2030-01-01T12:00:00Z"
    assert d["plan"] == {"id": pid, "name": d["plan"]["name"], "price": 30.0, "minutes": 43200,
                         "rate_per_minute": round(30.0 / 43200, 8)}  # fix2: per-minute rate
    assert d["effective_price"] == 30.0
    assert d["balance"] == -12.5 and d["debt"] == 12.5
    assert len(d["open_loans"]) == 1
    ln = d["open_loans"][0]
    assert ln["amount"] == 3.0 and ln["minutes"] == 3 * 1440 and ln["created_at"].endswith("Z")
    assert set(d["quota"]) >= {"has_quota", "daily_quota_mb", "used_today_mb"}
    assert d["online_sessions"] == 0
    assert set(d["channels"]) == {"sms", "whatsapp"}
    assert [t["label"] for t in d["message_templates"]] == [
        "ترحيب", "تذكير انتهاء", "تأكيد دفعة", "تذكير سداد", "صيانة"]
    assert s.username in d["message_templates"][0]["text_filled"]
    # owner rule (fix wave 2): one operation adds at most 1 year → 365, not 366
    assert d["max_free_loan_hours"] == 72 and d["max_debt_loan_days"] == 365
    perms = d["permissions"]
    assert set(perms) >= {"extend", "quota", "payment", "loan", "balance", "change_plan",
                          "send_message", "disconnect", "status", "delete", "rename",
                          "reset_password", "edit"}
    assert all(perms.values())  # env token = full access


def test_actions_context_not_found(client):
    _err(client.get("/api/v1/accounts/nobody_here/actions-context", headers=AUTH),
         404, "not_found")


def test_message_templates_match_the_web_dialog():
    from app.radius.services.subscriber_actions import MESSAGE_TEMPLATES
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(here, "app", "templates", "radius", "users_list.html"),
                encoding="utf-8").read()
    for t in MESSAGE_TEMPLATES:
        assert f'data-sms-tpl="{t["text"]}"' in html, t["key"]
        assert f"_('{t['label']}')" in html, t["key"]


# ─────────────── permissions (same decision as the web guard) ───────────────

def test_manager_permissions_follow_role(client):
    pid = _plan()
    s = _sub("perm_" + uuid4().hex[:6], plan_id=pid)
    mgr = _manager(("users.view", "users.extend"))
    hdr = _api_token_for(client, mgr)
    d = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=hdr))
    assert d["permissions"]["extend"] is True
    assert d["permissions"]["payment"] is False
    assert d["permissions"]["balance"] is False
    # allowed action works
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=hdr,
                      json={"mode": "duration", "minutes": 60, "charge_mode": "free"}))
    # denied actions → 403 forbidden (Arabic message), nothing written
    before = _state(s.username)
    for path, body in (("payment", {"amount": 10}), ("balance", {"amount": 5}),
                       ("loan", {"loan_type": "free", "days": 1}),
                       ("quota/topup", {"quota_mb": 100}),
                       ("change-plan", {"plan_id": pid, "policy": "neutral_keep_expiry"})):
        err = _err(client.post(f"/api/v1/accounts/{s.username}/{path}", headers=hdr, json=body),
                   403, "forbidden")
        assert "صلاحية" in err["message"]
    assert _state(s.username) == before


def test_api_denial_matches_web_denial(client):
    """The same manager is refused the same action on the web and in the API."""
    pid = _plan()
    a, b = _pair(plan_id=pid)
    mgr = _manager(("users.view",), password="mgr-pass")
    hdr = _api_token_for(client, mgr)
    web = client.post("/admin/radius/login", data={"username": mgr.username, "password": "mgr-pass"})
    assert web.status_code in {302, 303}
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token", "")
    r = client.post(f"/admin/radius/users/{a.username}/payments",
                    data={"_csrf_token": csrf, "amount": "10", "apply_to_radius": "1"},
                    headers=FETCH)
    assert r.status_code == 403
    _err(client.post(f"/api/v1/accounts/{b.username}/payment", headers=hdr,
                     json={"amount": 10}), 403, "forbidden")


def test_viewer_sees_no_actions(client):
    pid = _plan()
    s = _sub("view_" + uuid4().hex[:6], plan_id=pid)
    from app.radius.db.repos import admins_repo
    role = admins_repo.get_role_by_name("viewer")
    mgr = admins_repo.create_admin(username="v_" + uuid4().hex[:6], password="mgr-pass",
                                   full_name="V", role_id=role.id, is_super_admin=False)
    hdr = _api_token_for(client, mgr)
    # p01/D06: the context carries the subscriber's balance/loans/plan, so it
    # needs users.view like the web subscriber page — a bare viewer gets 403.
    _err(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=hdr),
         403, "forbidden")
    admins_repo.update_role(role.id, permissions=("dashboard.view", "users.view"))
    d = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=hdr))
    for key in ("extend", "payment", "loan", "balance", "quota", "change_plan", "send_message"):
        assert d["permissions"][key] is False, key
    _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=hdr,
                     json={"minutes": 60}), 403, "forbidden")


def test_requires_token(client):
    pid = _plan()
    s = _sub("tok_" + uuid4().hex[:6], plan_id=pid)
    assert client.post(f"/api/v1/accounts/{s.username}/extend",
                       json={"minutes": 60}).status_code == 401


# ─────────────── extend ───────────────

def test_extend_duration_and_validation(client):
    pid = _plan(price=30.0, days=30)
    s = _sub("ext_" + uuid4().hex[:6], plan_id=pid, balance=10)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                          json={"mode": "duration", "minutes": 1440, "charge_mode": "paid"}))
    # amount omitted → the web dialog's auto price: 30 × 1 day / 30 days = 1.00
    assert d["charged_amount"] == 1.0 and d["charge_mode"] == "paid"
    assert d["new_expire_at"] == "2030-01-02T12:00:00Z"
    assert _row(s.username)["balance"] == 9.0
    _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                     json={"mode": "duration", "minutes": 0}), 422, "validation_error")
    _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                     json={"minutes": 60, "charge_mode": "gift"}), 422, "validation_error")
    _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                     json={"mode": "expire_at", "expire_at": "not-a-date"}), 422, "validation_error")
    _err(client.post("/api/v1/accounts/ghost_user/extend", headers=AUTH,
                     json={"minutes": 60}), 404, "not_found")


def test_extend_expire_at_offset_is_an_instant(client):
    pid = _plan()
    s = _sub("exa_" + uuid4().hex[:6], plan_id=pid)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                          json={"mode": "expire_at", "expire_at": "2030-05-01T15:00:00+03:00"}))
    assert d["new_expire_at"] == "2030-05-01T12:00:00Z"
    # (within one year of the current expiry — owner rule: one extend ≤ 1 year)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                          json={"mode": "expire_at", "expire_at": "2030-06-01T08:30:00"}))
    assert d["new_expire_at"] == "2030-06-01T08:30:00Z"  # naive = UTC


@pytest.mark.parametrize("charge_mode,amount", [("free", "0"), ("paid", "3.00"), ("debt", "3.00")])
def test_extend_parity_web_vs_api(client, charge_mode, amount):
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    a, b = _pair(plan_id=pid, balance=5.0)
    r = client.post(f"/admin/radius/users/{a.username}/extend", data={
        "_csrf_token": csrf, "minutes": str(3 * 1440), "charge_mode": charge_mode,
        "amount": amount, "currency": "ILS", "notes": "n"})
    assert r.status_code in {302, 303}
    _data(client.post(f"/api/v1/accounts/{b.username}/extend", headers=AUTH, json={
        "mode": "duration", "minutes": 3 * 1440, "charge_mode": charge_mode,
        "amount": float(amount), "notes": "n"}))
    assert _state(a.username) == _state(b.username)
    assert _row(b.username)["expire_at"] == EXPIRE + timedelta(days=3)


def test_extend_parity_expire_at(client):
    csrf = _web_login(client)
    pid = _plan()
    a, b = _pair(plan_id=pid)
    from app.radius.core.system_config import to_local
    target = datetime(2030, 3, 1, 10, 0, 0)
    local = to_local(target, "%Y-%m-%dT%H:%M")
    r = client.post(f"/admin/radius/users/{a.username}/extend", data={
        "_csrf_token": csrf, "expire_at": local, "charge_mode": "free"})
    assert r.status_code in {302, 303}
    _data(client.post(f"/api/v1/accounts/{b.username}/extend", headers=AUTH, json={
        "mode": "expire_at", "expire_at": "2030-03-01T10:00:00Z", "charge_mode": "free"}))
    assert _state(a.username) == _state(b.username)
    assert _row(b.username)["expire_at"] == target


def test_extend_spend_gate_blocks_zero_trust_manager(client):
    pid = _plan()
    s = _sub("gate_" + uuid4().hex[:6], plan_id=pid, balance=50)
    mgr = _manager(("users.view", "users.extend"))
    hdr = _api_token_for(client, mgr)
    before = _state(s.username)
    err = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=hdr, json={
        "minutes": 1440, "charge_mode": "paid", "amount": 5}), 403, "spend_blocked")
    assert err["message"]
    assert _state(s.username) == before


# ─────────────── change plan / quota ───────────────

def test_change_plan_and_validation(client):
    cheap, pricey = _plan(price=30.0), _plan(price=60.0)
    s = _sub("cp_" + uuid4().hex[:6], plan_id=cheap)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                          json={"plan_id": pricey, "policy": "higher_keep_expiry"}))
    assert d["plan_id"] == pricey and d["new_expire_at"] == "2030-01-01T12:00:00Z"
    assert d["debt_amount"] == 0.0
    _err(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                     json={"plan_id": cheap, "policy": "bogus"}), 422, "validation_error")
    _err(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                     json={"plan_id": "x", "policy": "neutral_keep_expiry"}), 422, "validation_error")


def test_change_plan_parity(client):
    csrf = _web_login(client)
    cheap, pricey = _plan(price=30.0), _plan(price=60.0)
    a, b = _pair(plan_id=cheap, expire_at=datetime.utcnow() + timedelta(days=10))
    client.post(f"/admin/radius/users/{a.username}/change-plan", data={
        "_csrf_token": csrf, "plan_id": str(pricey), "policy": "higher_debt"})
    _data(client.post(f"/api/v1/accounts/{b.username}/change-plan", headers=AUTH,
                      json={"plan_id": pricey, "policy": "higher_debt"}))
    sa, sb = _state(a.username), _state(b.username)
    assert sa["sub"]["plan_id"] == sb["sub"]["plan_id"] == pricey
    assert sa["ledger"] == sb["ledger"] and sa["ledger"]
    assert abs(sa["sub"]["balance"] - sb["sub"]["balance"]) <= 0.02


def test_quota_topup_reset_and_parity(client):
    csrf = _web_login(client)
    pid = _plan()
    # A top-up ADDS to the quota in force (here the plan's 1024 MB) — a plan
    # without any quota refuses a top-up (see test_stress_fix_money).
    from app.radius.db.connection import db
    db().execute("UPDATE access_plans SET quota_total_mb = 1024 WHERE id = ?", (pid,))
    a, b = _pair(plan_id=pid, balance=20)
    client.post(f"/admin/radius/users/{a.username}/quota/topup", data={
        "_csrf_token": csrf, "quota_mb": "500", "quota_target": "combined",
        "charge_mode": "paid", "amount": "4", "currency": "ILS", "notes": ""})
    d = _data(client.post(f"/api/v1/accounts/{b.username}/quota/topup", headers=AUTH, json={
        "quota_mb": 500, "quota_target": "combined", "charge_mode": "paid", "amount": 4}))
    assert d["quota"]["combined_quota_mb"] == 1524 and d["balance"] == 16.0
    client.post(f"/admin/radius/users/{a.username}/quota/reset-daily", data={
        "_csrf_token": csrf, "charge_mode": "debt", "amount": "2", "currency": "ILS"})
    d = _data(client.post(f"/api/v1/accounts/{b.username}/quota/reset-daily", headers=AUTH,
                          json={"charge_mode": "debt", "amount": 2}))
    assert d["balance"] == 14.0
    assert _state(a.username) == _state(b.username)
    _err(client.post(f"/api/v1/accounts/{b.username}/quota/topup", headers=AUTH,
                     json={"quota_mb": 0}), 422, "validation_error")
    _err(client.post(f"/api/v1/accounts/{b.username}/quota/topup", headers=AUTH,
                     json={"quota_mb": 1.5}), 422, "validation_error")
    _err(client.post(f"/api/v1/accounts/{b.username}/quota/reset-daily", headers=AUTH,
                     json={"charge_mode": "paid", "amount": 0}), 422, "validation_error")


# ─────────────── payment ───────────────

def test_payment_parity_with_loan_actions_and_debt(client):
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    a, b = _pair(plan_id=pid, balance=-6.0)
    la1, la2 = _open_loan(a.username, days=2), _open_loan(a.username, days=1)
    lb1, lb2 = _open_loan(b.username, days=2), _open_loan(b.username, days=1)
    r = client.post(f"/admin/radius/users/{a.username}/payments", headers=FETCH, data={
        "_csrf_token": csrf, "amount": "30", "currency": "ILS", "method": "cash",
        "notes": "", "apply_to_radius": "1", "settle_balance": "1",
        "loan_actions": json.dumps([{"loan_id": la1, "action": "settle"},
                                    {"loan_id": la2, "action": "writeoff"}])})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    d = _data(client.post(f"/api/v1/accounts/{b.username}/payment", headers=AUTH, json={
        "amount": 30, "method": "cash", "settle_balance": True,
        "loan_actions": [{"loan_id": lb1, "action": "settle"},
                         {"loan_id": lb2, "action": "forgive"}]}), 201)
    assert d["loans_resolved"] == [{"loan_id": lb1, "action": "settle"},
                                   {"loan_id": lb2, "action": "forgive"}]
    assert d["settled_loans_total"] == 2.0 and d["debt_settled"] == 6.0
    # 30 − 2 (loan) − 6 (debt) = 22 buys ~22 days (floor rounding of the service)
    assert abs(d["added_minutes"] - 22 * 1440) <= 1
    assert d["new_expire_at"] == (EXPIRE + timedelta(minutes=d["added_minutes"])).isoformat() + "Z"
    assert d["payment"]["amount"] == 30.0
    assert d["message"].startswith("تم تسجيل الدفعة")
    assert _state(a.username) == _state(b.username)


def test_payment_validation(client):
    pid = _plan()
    s = _sub("payv_" + uuid4().hex[:6], plan_id=pid)
    url = f"/api/v1/accounts/{s.username}/payment"
    _err(client.post(url, headers=AUTH, json={"amount": 0}), 422, "validation_error")
    _err(client.post(url, headers=AUTH, json={"amount": 5, "method": "crypto"}), 422,
         "validation_error")
    _err(client.post(url, headers=AUTH, json={"amount": 5, "loan_actions": [{"loan_id": 1,
         "action": "burn"}]}), 422, "validation_error")
    _err(client.post(url, headers=AUTH, json={"amount": 5, "loan_actions": "x"}), 422,
         "validation_error")


# ─────────────── balance ───────────────

def test_balance_parity_with_loan_settle(client):
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    a, b = _pair(plan_id=pid, balance=1.0)
    la, lb = _open_loan(a.username, days=3), _open_loan(b.username, days=3)
    client.post(f"/admin/radius/users/{a.username}/balance/add", data={
        "_csrf_token": csrf, "amount": "10", "currency": "ILS", "notes": "cash",
        "loan_actions": json.dumps([{"loan_id": la, "action": "settle"}])})
    d = _data(client.post(f"/api/v1/accounts/{b.username}/balance", headers=AUTH, json={
        "amount": 10, "notes": "cash", "loan_actions": [{"loan_id": lb, "action": "settle"}]}))
    assert d["balance"] == 8.0 and d["credited"] == 7.0
    assert d["loans_resolved"] == [{"loan_id": lb, "action": "settle"}]
    assert _state(a.username) == _state(b.username)
    _err(client.post(f"/api/v1/accounts/{b.username}/balance", headers=AUTH,
                     json={"amount": -1}), 422, "validation_error")


# ─────────────── loan ───────────────

@pytest.mark.parametrize("loan_type", ["free", "debt"])
def test_loan_parity_web_vs_api(client, loan_type):
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    a, b = _pair(plan_id=pid)
    debt = loan_type == "debt"
    r = client.post(f"/admin/radius/users/{a.username}/loans", headers=FETCH, data={
        "_csrf_token": csrf, "days": "2", "hours": "3",
        "amount": "2.13" if debt else "0", "price_from_days": "1" if debt else "0",
        "currency": "ILS", "reason": "r", "apply_to_radius": "1"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    d = _data(client.post(f"/api/v1/accounts/{b.username}/loan", headers=AUTH, json={
        "loan_type": loan_type, "days": 2, "hours": 3, "reason": "r"}), 201)
    assert d["pending_approval"] is False
    assert d["loan"]["duration_minutes"] == 2 * 1440 + 180
    assert (float(d["loan"]["amount"]) > 0) is debt
    assert _state(a.username) == _state(b.username)
    assert _row(b.username)["expire_at"] == EXPIRE + timedelta(minutes=2 * 1440 + 180)


def test_loan_validation_and_caps(client):
    pid = _plan()
    s = _sub("lv_" + uuid4().hex[:6], plan_id=pid)
    url = f"/api/v1/accounts/{s.username}/loan"
    _err(client.post(url, headers=AUTH, json={"loan_type": "gift", "days": 1}), 422,
         "validation_error")
    _err(client.post(url, headers=AUTH, json={"loan_type": "free", "days": 0, "hours": 0}),
         422, "validation_error")
    # free loans are capped at 72 h (same service rule as the web)
    err = _err(client.post(url, headers=AUTH, json={"loan_type": "free", "days": 4}), 422,
               "validation_error")
    assert "72" in err["message"]
    _data(client.post(url, headers=AUTH, json={"loan_type": "debt", "days": 30}), 201)


@pytest.mark.parametrize("perms", [("users.view",), ("users.view", "users.loans")])
def test_loan_manager_decision_matches_web(client, perms):
    """Whatever the web decides for this manager's loan, the API decides too."""
    pid = _plan()
    a, b = _pair(plan_id=pid)
    mgr = _manager(perms)
    hdr = _api_token_for(client, mgr)
    assert client.post("/admin/radius/login", data={
        "username": mgr.username, "password": "mgr-pass"}).status_code in {302, 303}
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token", "")
    web = client.post(f"/admin/radius/users/{a.username}/loans", headers=FETCH, data={
        "_csrf_token": csrf, "days": "1", "hours": "0", "amount": "0",
        "price_from_days": "0", "currency": "ILS", "reason": "", "apply_to_radius": "1"})
    api = client.post(f"/api/v1/accounts/{b.username}/loan", headers=hdr,
                      json={"loan_type": "free", "days": 1})
    assert (web.status_code == 403) == (api.status_code == 403), (web.status_code, api.get_json())
    if "users.loans" not in perms:
        assert api.status_code == 403
    assert _state(a.username) == _state(b.username)


# ─────────────── message / credentials ───────────────

def test_message_validation_and_send(client):
    pid = _plan()
    s = _sub("msg_" + uuid4().hex[:6], plan_id=pid)
    url = f"/api/v1/accounts/{s.username}/message"
    _err(client.post(url, headers=AUTH, json={"channel": "sms", "message": "  "}), 422,
         "validation_error")
    _err(client.post(url, headers=AUTH, json={"channel": "pigeon", "message": "hi"}), 422,
         "validation_error")
    nomob = _sub("nomob_" + uuid4().hex[:6], plan_id=pid, mobile="")
    err = _err(client.post(f"/api/v1/accounts/{nomob.username}/message", headers=AUTH,
                           json={"channel": "sms", "message": "hi"}), 422, "validation_error")
    assert err["message"] == "لا يوجد رقم جوال لهذا المشترك."
    res = client.post(url, headers=AUTH, json={"channel": "sms", "message": "hi {username}"})
    body = res.get_json()
    # Queued like the web (or a clean 422 if the tenant has no SMS channel).
    assert res.status_code in {200, 422}, body
    if res.status_code == 200:
        assert body["data"]["queued_count"] >= 1 and body["data"]["sent"] is True


def test_send_credentials_reports_reason(client):
    pid = _plan()
    s = _sub("cred_" + uuid4().hex[:6], plan_id=pid)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/send-credentials", headers=AUTH, json={}))
    assert d["sent"] is False and d["reason"] == "not_connected" and d["message"]
    assert isinstance(d["segments"], int)
    nomob = _sub("credn_" + uuid4().hex[:6], plan_id=pid, mobile="")
    d = _data(client.post(f"/api/v1/accounts/{nomob.username}/send-credentials", headers=AUTH))
    assert d["reason"] == "no_mobile"


def test_send_credentials_web_route_unchanged(client):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub("credw_" + uuid4().hex[:6], plan_id=pid)
    r = client.post(f"/admin/radius/users/{s.username}/send-credentials",
                    data={"_csrf_token": csrf}, headers=FETCH)
    assert r.status_code == 200
    assert r.get_json()["ok"] is False and r.get_json()["reason"] == "not_connected"
    r = client.post("/admin/radius/users/ghost_nobody/send-credentials",
                    data={"_csrf_token": csrf}, headers=FETCH)
    assert r.status_code == 404


# ─────────────── rename / disconnect ───────────────

def test_rename(client):
    pid = _plan()
    s = _sub("rn_" + uuid4().hex[:6], plan_id=pid)
    new = "rn2_" + uuid4().hex[:6]
    d = _data(client.post(f"/api/v1/accounts/{s.username}/rename", headers=AUTH,
                          json={"new_username": new}))
    assert d == {"username": new, "old_username": s.username, "renamed": True,
                 "had_live_session": False}
    from app.radius.db.repos import subscribers_repo
    assert subscribers_repo.get_subscriber(1, new) is not None
    _err(client.post(f"/api/v1/accounts/{new}/rename", headers=AUTH,
                     json={"new_username": "bad name!"}), 422, "validation_error")
    other = _sub("rn3_" + uuid4().hex[:6], plan_id=pid)
    _err(client.post(f"/api/v1/accounts/{new}/rename", headers=AUTH,
                     json={"new_username": other.username}), 422, "validation_error")


def test_disconnect(client, monkeypatch):
    pid = _plan()
    s = _sub("dc_" + uuid4().hex[:6], plan_id=pid)
    from app.radius.db.connection import db
    db().execute("INSERT INTO radacct(tenant_id, username, acctsessionid, acctstarttime) "
                 "VALUES(1, ?, 'sess-1', ?)", (s.username, datetime.utcnow().isoformat()))
    calls = []
    from app.radius.services import sessions as sessions_mod
    monkeypatch.setattr(sessions_mod.OnlineSessionsService, "disconnect",
                        lambda self, **kw: calls.append(kw))
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["online_sessions"] == 1
    d = _data(client.post(f"/api/v1/accounts/{s.username}/disconnect", headers=AUTH, json={}))
    assert d["disconnected"] == 1
    assert calls and calls[0]["username"] == s.username and calls[0]["session_id"] is None

    from app.radius.core.errors import RadiusError

    def _boom(self, **kw):
        raise RadiusError("تعذّر قطع الجلسة (timeout)")
    monkeypatch.setattr(sessions_mod.OnlineSessionsService, "disconnect", _boom)
    _err(client.post(f"/api/v1/accounts/{s.username}/disconnect", headers=AUTH, json={}),
         502, "disconnect_failed")


@pytest.mark.parametrize("path", ["payment", "balance"])
def test_loan_action_forgive_is_a_writeoff_never_a_deferral(client, path):
    """App «مسامحة» = ``forgive`` → the web/service ``writeoff``; ``writeoff`` is
    accepted as-is too; ``defer`` leaves the loan open."""
    pid = _plan(price=30.0, days=30)
    s = _sub("fg_" + uuid4().hex[:6], plan_id=pid)
    forgive, writeoff, defer = (_open_loan(s.username, days=1) for _ in range(3))
    d = _data(client.post(f"/api/v1/accounts/{s.username}/{path}", headers=AUTH, json={
        "amount": 10, "loan_actions": [{"loan_id": forgive, "action": "forgive"},
                                       {"loan_id": writeoff, "action": "writeoff"},
                                       {"loan_id": defer, "action": "defer"}]}),
        201 if path == "payment" else 200)
    assert d["loans_resolved"] == [{"loan_id": forgive, "action": "forgive"},
                                   {"loan_id": writeoff, "action": "forgive"}]
    from app.radius.services.accounting import AccountingService
    acc = AccountingService(1)
    assert acc.get_loan(forgive)["status"] == acc.get_loan(writeoff)["status"] != "open"
    assert acc.get_loan(defer)["status"] == "open"
