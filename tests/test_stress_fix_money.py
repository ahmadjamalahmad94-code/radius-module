"""Stress-campaign money fixes (2026-09-28) — regression tests.

Each test pins one business rule the stress run broke (a03/a04/a05/a10/a11):
duplicate-username create, cross-subscriber / duplicate loan actions, partial /
over / free-loan settlement, settle + approval races, the loans center gates and
dry run, unlimited / disabled subscribers, ledger void, idempotency keys, quota
top-up, plan delete / names, paid-from-balance, currency, report totals/paging,
revenue, local-day sales, events date filter, distributor limits and negative
amounts. API and web route are both covered where the web shares the path.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "money-fix-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
FETCH = {"X-Requested-With": "fetch"}
EXPIRE = datetime(2030, 1, 1, 12, 0, 0)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "money.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "money-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_money")
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
        application.config["TEST_OWNER"] = "owner_money"
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────── helpers ───────────────

def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_money", "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _db():
    from app.radius.db.connection import db
    return db()


def _plan(name=None, *, price=30.0, days=30, currency="ILS", quota_mb=0) -> int:
    now = datetime.utcnow().isoformat()
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, quota_total_mb, enabled, created_at, updated_at) "
        "VALUES(1,?,?,?,?,?,?,1,?,?)",
        (name or "p_" + uuid4().hex[:6], days * 1440, days, price, currency,
         quota_mb, now, now))
    return int(cur.lastrowid)


def _sub(username=None, *, plan_id, balance=0.0, expire_at=EXPIRE, status="enabled"):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "u_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Money User", mobile="0599000000", status=status, expire_at=expire_at))
    _db().execute("UPDATE subscribers SET balance=? WHERE tenant_id=1 AND username=?",
                  (float(balance), username))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _loan(username, *, days=2, amount=None, free=False) -> int:
    from app.radius.services.accounting import AccountingService
    body = {"username": username, "days": str(days), "hours": "0", "currency": "ILS",
            "reason": "seed", "apply_to_radius": False}
    if free:
        body.update({"price_from_days": False, "amount": 0})
    elif amount is not None:
        body.update({"price_from_days": False, "amount": amount})
    else:
        body.update({"price_from_days": True, "amount": 0})
    return int(AccountingService(1).create_loan(body, actor="seed")["id"])


def _loan_row(loan_id):
    from app.radius.db.repos import accounting_repo
    return accounting_repo.get_loan(1, loan_id)


def _count(sql, *args) -> int:
    return int(_db().execute(sql, args).fetchone()[0] or 0)


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status, code=None):
    body = res.get_json()
    assert res.status_code == status, body
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


# ─────────────── 1. create with an existing username ───────────────

def test_create_existing_username_is_409_and_never_overwrites(client):
    pid = _plan()
    s = _sub(plan_id=pid, balance=25)
    for name in (s.username, s.username.upper(), f"  {s.username} "):
        err = _err(client.post("/api/v1/accounts", headers=AUTH, json={
            "username": name, "password": "pwB99999", "full_name": "Overwritten"}), 409)
        assert "مستخدم مسبقًا" in err["message"]
    after = _get(s.username)
    assert float(after.balance) == 25.0 and after.expire_at == EXPIRE
    assert after.plan_id == pid and after.full_name == "Money User"
    assert after.password == "secret"


def test_create_with_card_username_is_409(client):
    now = datetime.utcnow().isoformat()
    pid = _plan()
    bid = _db().execute("INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
                        "created_at) VALUES(1,'B-C-1',?,1,?)", (pid, now)).lastrowid
    _db().execute("INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, used, "
                  "created_at) VALUES(1, ?, 'card777', 'pw', ?, 0, ?)", (bid, pid, now))
    _err(client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "card777", "password": "pw1234567"}), 409)
    assert _count("SELECT COUNT(*) FROM subscribers WHERE username='card777'") == 0


def test_web_create_existing_username_refused(client):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid, balance=25)
    res = client.post("/admin/radius/users", data={
        "_csrf_token": csrf, "username": s.username, "password": "newpass123",
        "full_name": "Overwritten", "plan_id": str(pid), "status": "enabled",
        "user_type": "subscriber"})
    assert res.status_code == 400
    after = _get(s.username)
    assert float(after.balance) == 25.0 and after.full_name == "Money User"


# ─────────────── 2 + 3. loan actions: ownership + duplicates ───────────────

def test_payment_with_foreign_loan_is_refused(client):
    pid = _plan()
    a, b = _sub(plan_id=pid), _sub(plan_id=pid)
    lb = _loan(b.username, days=3)
    err = _err(client.post(f"/api/v1/accounts/{a.username}/payment", headers=AUTH, json={
        "amount": 30, "loan_actions": [{"loan_id": lb, "action": "settle"}]}), 422)
    assert "لا تخصّ" in err["message"]
    _err(client.post(f"/api/v1/accounts/{a.username}/balance", headers=AUTH, json={
        "amount": 10, "loan_actions": [{"loan_id": lb, "action": "forgive"}]}), 422)
    assert _loan_row(lb)["status"] == "open"
    assert _count("SELECT COUNT(*) FROM payment_transactions WHERE username=?", a.username) == 0
    assert float(_get(a.username).balance) == 0.0


def test_web_payment_and_settle_refuse_foreign_loan(client):
    csrf = _web_login(client)
    pid = _plan()
    a, b = _sub(plan_id=pid), _sub(plan_id=pid)
    lb = _loan(b.username, days=3)
    r = client.post(f"/admin/radius/users/{a.username}/payments", headers=FETCH, data={
        "_csrf_token": csrf, "amount": "30", "method": "cash", "apply_to_radius": "1",
        "loan_actions": json.dumps([{"loan_id": lb, "action": "settle"}])})
    assert r.status_code >= 400 and r.get_json()["ok"] is False
    r = client.post(f"/admin/radius/users/{a.username}/loans/{lb}/settle",
                    data={"_csrf_token": csrf, "amount": ""})
    assert r.status_code in {302, 303}
    assert _loan_row(lb)["status"] == "open"
    assert _count("SELECT COUNT(*) FROM settlement_entries WHERE loan_id=?", lb) == 0


def test_duplicate_loan_id_is_deducted_once(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    ln = _loan(s.username, days=5)  # 5.00
    d = _data(client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH, json={
        "amount": 20, "loan_actions": [{"loan_id": ln, "action": "settle"},
                                       {"loan_id": ln, "action": "settle"}]}), 201)
    assert d["settled_loans_total"] == 5.0
    assert d["added_minutes"] == 15 * 1440  # (20 − 5) × 1440 min/ILS
    meta = json.loads(_db().execute(
        "SELECT metadata_json FROM payment_transactions WHERE username=?",
        (s.username,)).fetchone()[0])
    assert meta["loan_settled_total"] == 5.0
    ln2 = _loan(s.username, days=2)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH, json={
        "amount": 20, "loan_actions": [{"loan_id": ln2, "action": "settle"}] * 3}))
    assert d["credited"] == 18.0 and d["settled_loans_total"] == 2.0
    _err(client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH, json={
        "amount": 20, "loan_actions": [{"loan_id": ln, "action": "settle"},
                                       {"loan_id": ln, "action": "forgive"}]}), 422)


# ─────────────── 4. partial / over / free-loan settlement ───────────────

def test_partial_settle_keeps_the_remainder_open(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    ln = _loan(s.username, days=6)  # 6.00
    d = _data(client.post(f"/api/v1/loans/{ln}/settle", headers=AUTH, json={"amount": 2}), 201)
    assert d["settlement"]["loan_status"] == "open"
    assert d["settlement"]["loan_outstanding"] == 4.0
    row = _loan_row(ln)
    assert row["status"] == "open" and row["outstanding"] == 4.0
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["open_loans"][0]["amount"] == 4.0
    assert ctx["open_loans"][0]["original_amount"] == 6.0
    _err(client.post(f"/api/v1/loans/{ln}/settle", headers=AUTH, json={"amount": 5}), 422)
    d = _data(client.post(f"/api/v1/loans/{ln}/settle", headers=AUTH, json={}), 201)
    assert d["settlement"]["amount"] == 4.0 and d["settlement"]["loan_status"] == "settled"
    _err(client.post(f"/api/v1/loans/{ln}/settle", headers=AUTH, json={}), 409)
    total = _db().execute("SELECT SUM(amount) FROM settlement_entries WHERE loan_id=?",
                          (ln,)).fetchone()[0]
    assert round(total, 2) == 6.0


def test_over_settle_and_money_on_free_loan_are_refused(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    ln = _loan(s.username, days=3)  # 3.00
    _err(client.post(f"/api/v1/loans/{ln}/settle", headers=AUTH, json={"amount": 5}), 422)
    free = _loan(s.username, days=1, free=True)
    err = _err(client.post(f"/api/v1/loans/{free}/settle", headers=AUTH,
                           json={"amount": 7.5}), 422)
    assert "مجّانيّة" in err["message"]
    _err(client.post(f"/api/v1/loans/{ln}/settle", headers=AUTH,
                     json={"currency": "btc"}), 422)
    assert _count("SELECT COUNT(*) FROM settlement_entries WHERE loan_id IN (?,?)", ln, free) == 0
    _data(client.post(f"/api/v1/loans/{free}/settle", headers=AUTH, json={}), 201)
    assert _loan_row(free)["status"] == "settled"
    _err(client.post("/api/v1/loans/99999999/settle", headers=AUTH, json={}), 404)


def test_payment_smaller_than_loan_settles_it_partially(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    ln = _loan(s.username, days=28)  # 28.00
    d = _data(client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH, json={
        "amount": 10, "loan_actions": [{"loan_id": ln, "action": "settle"}]}), 201)
    assert d["settled_loans_total"] == 10.0
    row = _loan_row(ln)
    assert row["status"] == "open" and row["outstanding"] == 18.0


# ─────────────── 5. races (conditional updates) ───────────────

def test_settle_with_a_stale_open_snapshot_settles_once(app):
    from app.radius.core.errors import RadiusConflict
    from app.radius.db.repos import accounting_repo
    pid = _plan()
    s = _sub(plan_id=pid)
    ln = _loan(s.username, days=4)
    stale = accounting_repo.get_loan(1, ln)  # both «requests» read it open
    accounting_repo.settle_loan(tenant_id=1, loan=stale, amount=4.0, currency="ILS",
                                method="cash", created_by="t1")
    with pytest.raises(RadiusConflict):
        accounting_repo.settle_loan(tenant_id=1, loan=stale, amount=4.0, currency="ILS",
                                    method="cash", created_by="t2")
    with pytest.raises(RadiusConflict):
        accounting_repo.writeoff_loan(tenant_id=1, loan=stale, currency="ILS", created_by="t3")
    assert _count("SELECT COUNT(*) FROM settlement_entries WHERE loan_id=?", ln) == 1
    assert _count("SELECT COUNT(*) FROM accounting_ledger_entries WHERE related_id=? "
                  "AND related_type='loan'", ln) == 1


def test_parallel_settles_settle_once(app):
    import threading
    from app.radius.services.accounting import AccountingService
    pid = _plan()
    s = _sub(plan_id=pid)
    ln = _loan(s.username, days=2)
    results = []

    def worker():
        with app.app_context():
            try:
                AccountingService(1).settle_loan(ln, {}, actor="race")
                results.append("ok")
            except Exception as exc:  # noqa: BLE001
                results.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1, results
    assert _count("SELECT COUNT(*) FROM settlement_entries WHERE loan_id=?", ln) == 1


def test_approving_one_request_twice_creates_one_loan(app, monkeypatch):
    from app.radius.services import manager_approvals as ap
    pid = _plan()
    s = _sub(plan_id=pid)
    req = ap.enqueue(1, "subscriber.loan", amount_minor=36600, payload={
        "username": s.username, "days": "3", "hours": "0", "amount": 3,
        "price_from_days": True, "currency": "ILS", "apply_to_radius": True,
        "dry_run": False}, summary="t")
    stale = dict(req)
    monkeypatch.setattr(ap, "get", lambda *_a, **_k: dict(stale))  # both see «pending»
    ap.approve(req["id"], decided_by=1)
    with pytest.raises(ap.ApprovalError):
        ap.approve(req["id"], decided_by=1)
    assert _count("SELECT COUNT(*) FROM loan_entries WHERE username=?", s.username) == 1


# ─────────────── 6. loans center: gates + dry run ───────────────

def test_loans_center_dry_run_writes_nothing(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    d = _data(client.post("/api/v1/loans", headers=AUTH, json={
        "username": s.username, "days": 3, "amount": 9, "apply_to_radius": True,
        "dry_run": True}), 200)
    assert d["dry_run"] is True and d["loan"]["id"] is None
    assert d["loan"]["activation_result"]["status"] == "planned"
    assert _count("SELECT COUNT(*) FROM loan_entries WHERE username=?", s.username) == 0
    assert _count("SELECT COUNT(*) FROM accounting_ledger_entries WHERE username=?",
                  s.username) == 0
    assert _get(s.username).expire_at == EXPIRE


def test_loans_center_requires_the_loans_permission(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    no_perm = _manager(("users.view",))
    _err(client.post("/api/v1/loans", headers=_token_for(client, no_perm), json={
        "username": s.username, "days": 366, "amount": 9999, "apply_to_radius": True}), 403)
    assert _count("SELECT COUNT(*) FROM loan_entries WHERE username=?", s.username) == 0


def test_loans_center_uses_permission_approval_queue_and_spend_gate(client, monkeypatch):
    from app.radius.services import manager_grants
    from app.radius.services.business_os_finance import money_to_minor
    # The owner's per-action switch for «سلفة» is off by default for a new
    # manager; turn it on so the request reaches the money gates under test.
    monkeypatch.setattr(manager_grants, "action_permitted", lambda *a, **k: True)
    pid = _plan()
    s = _sub(plan_id=pid)
    mgr = _manager(("users.view", "users.loans"))
    hdr = _token_for(client, mgr)
    # Zero-trust manager (no funding) → the advance gate blocks it.
    _err(client.post("/api/v1/loans", headers=hdr, json={
        "username": s.username, "days": 2, "amount": 5, "apply_to_radius": True}),
        403, "spend_blocked")
    # Above the owner's approval threshold → queued, nothing created.
    _db().execute(
        "INSERT INTO manager_distributor_policies(tenant_id, entity_type, entity_id, "
        "require_approval_above_minor, created_at) VALUES(1,'manager',?,?,?)",
        (mgr.id, money_to_minor(10), datetime.utcnow().isoformat()))
    d = _data(client.post("/api/v1/loans", headers=hdr, json={
        "username": s.username, "days": 366, "amount": 9999, "apply_to_radius": True}), 202)
    assert d["pending_approval"] is True and d["loan"] is None
    assert _count("SELECT COUNT(*) FROM loan_entries WHERE username=?", s.username) == 0
    assert _count("SELECT COUNT(*) FROM manager_pending_approvals WHERE admin_id=?", mgr.id) == 1
    assert _get(s.username).expire_at == EXPIRE


def test_loans_list_totals_cover_every_row_and_limit_is_clamped(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    for _ in range(4):
        _loan(s.username, days=2)
    d = _data(client.get("/api/v1/loans?status=open&limit=2", headers=AUTH))
    assert d["count"] == 2 and d["total_count"] == 4 and d["has_more"] is True
    assert d["totals"]["outstanding"] == 8.0
    d = _data(client.get("/api/v1/loans?limit=-1", headers=AUTH))
    assert d["count"] == 1
    _err(client.get("/api/v1/loans?status=garbage", headers=AUTH), 422)
    assert _data(client.get("/api/v1/payments?limit=-5", headers=AUTH))["count"] <= 1
    assert _data(client.get("/api/v1/ledger?limit=-5", headers=AUTH))["count"] <= 1


# ─────────────── 7. unlimited + disabled subscribers ───────────────

def test_loan_on_unlimited_subscriber_imposes_no_expiry(client):
    pid = _plan()
    s = _sub(plan_id=pid, expire_at=None)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "free", "hours": 1}), 201)
    assert "غير محدود" in d["message"]
    assert d["loan"]["activation_result"]["reason"] == "unlimited_subscriber"
    assert _get(s.username).expire_at is None  # was: now + 1 h
    d = _data(client.post("/api/v1/loans", headers=AUTH, json={
        "username": s.username, "days": 2, "amount": 4, "apply_to_radius": True}), 201)
    assert _get(s.username).expire_at is None


def test_loan_on_disabled_subscriber_does_not_re_enable(client):
    pid = _plan()
    s = _sub(plan_id=pid, status="disabled")
    _data(client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH, json={
        "loan_type": "free", "hours": 2}), 201)
    after = _get(s.username)
    assert after.status == "disabled"
    assert after.expire_at == EXPIRE + timedelta(hours=2)


# ─────────────── 8. ledger void ───────────────

def test_ledger_void_once_marks_payment_and_takes_time_back(client):
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH,
                          json={"amount": 30}), 201)
    pay_id = d["payment"]["id"]
    assert _get(s.username).expire_at == EXPIRE + timedelta(days=30)
    entry_id = d["payment"]["ledger_entry_id"]
    v = _data(client.post("/api/v1/ledger/void", headers=AUTH, json={"entry_id": entry_id}), 201)
    assert v["entry"]["reversal_of_entry_id"] == entry_id
    status = _db().execute("SELECT status FROM payment_transactions WHERE id=?",
                           (pay_id,)).fetchone()[0]
    assert status == "voided"
    assert _get(s.username).expire_at == EXPIRE  # exactly the 30 days taken back
    _err(client.post("/api/v1/ledger/void", headers=AUTH, json={"entry_id": entry_id}), 409)
    _err(client.post("/api/v1/ledger/void", headers=AUTH,
                     json={"entry_id": v["entry"]["id"]}), 422)
    _err(client.post(f"/api/v1/payments/{pay_id}/void", headers=AUTH, json={}), 409)
    assert _count("SELECT COUNT(*) FROM accounting_ledger_entries WHERE reversal_of_entry_id=?",
                  entry_id) == 1
    rows = _data(client.get("/api/v1/reports/payments", headers=AUTH))["items"]
    assert [r["total"] for r in rows if r["username"] == s.username] == [0.0]


def test_profit_loss_nets_a_voided_payment_to_zero(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    d = _data(client.post("/api/v1/payments", headers=AUTH,
                          json={"username": s.username, "amount": 99.99}), 201)
    _data(client.post(f"/api/v1/payments/{d['payment']['id']}/void", headers=AUTH,
                      json={}), 201)
    pl = _data(client.get("/api/v1/reports/profit-loss", headers=AUTH))["items"][0]
    assert round(pl["net"], 2) == 0.0 and round(pl["credits"], 2) == 0.0


def test_web_ledger_void_refuses_second_void(client):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid, balance=0)
    _data(client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH,
                      json={"amount": 10}))
    entry_id = _db().execute("SELECT id FROM accounting_ledger_entries WHERE username=? "
                             "AND entry_type='cash_balance'", (s.username,)).fetchone()[0]
    for _ in range(2):
        client.post("/admin/radius/finance/ledger/void",
                    data={"_csrf_token": csrf, "entry_id": str(entry_id)})
    assert _count("SELECT COUNT(*) FROM accounting_ledger_entries WHERE reversal_of_entry_id=?",
                  entry_id) == 1


# ─────────────── 9. idempotency ───────────────

def test_idempotency_key_replays_the_first_payment(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    hdr = dict(AUTH, **{"Idempotency-Key": "pay-" + uuid4().hex})
    first = client.post(f"/api/v1/accounts/{s.username}/payment", headers=hdr,
                        json={"amount": 25})
    second = client.post(f"/api/v1/accounts/{s.username}/payment", headers=hdr,
                         json={"amount": 25})
    assert first.status_code == second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.get_json()["data"]["payment"]["id"] == second.get_json()["data"]["payment"]["id"]
    assert _count("SELECT COUNT(*) FROM payment_transactions WHERE username=?", s.username) == 1
    other = dict(AUTH, **{"Idempotency-Key": "pay-" + uuid4().hex})
    _data(client.post(f"/api/v1/accounts/{s.username}/payment", headers=other,
                      json={"amount": 25}), 201)
    assert _count("SELECT COUNT(*) FROM payment_transactions WHERE username=?", s.username) == 2
    body = {"amount": 5, "client_request_id": "bal-" + uuid4().hex}
    _data(client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH, json=body))
    _data(client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH, json=body))
    assert float(_get(s.username).balance) == 5.0


def test_failed_attempt_releases_the_idempotency_key(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    hdr = dict(AUTH, **{"Idempotency-Key": "k-" + uuid4().hex})
    _err(client.post(f"/api/v1/accounts/{s.username}/payment", headers=hdr,
                     json={"amount": -1}), 422)
    _data(client.post(f"/api/v1/accounts/{s.username}/payment", headers=hdr,
                      json={"amount": 5}), 201)


# ─────────────── 10. quota top-up ───────────────

def test_quota_topup_adds_to_the_plan_quota(client):
    pid = _plan(quota_mb=1024)
    s = _sub(plan_id=pid)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/quota/topup", headers=AUTH,
                          json={"quota_mb": 100, "quota_target": "combined"}))
    assert d["quota"]["combined_quota_mb"] == 1124
    d = _data(client.post(f"/api/v1/accounts/{s.username}/quota/topup", headers=AUTH,
                          json={"quota_mb": 100}))
    assert d["quota"]["combined_quota_mb"] == 1224
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["quota"]["quota_mb"] == 1224 and ctx["quota"]["daily_quota_mb"] is None
    _err(client.post(f"/api/v1/accounts/{s.username}/quota/topup", headers=AUTH,
                     json={"quota_mb": 50, "quota_target": "download"}), 422)


def test_quota_topup_without_any_quota_is_refused(client):
    pid = _plan(quota_mb=0)
    s = _sub(plan_id=pid)
    err = _err(client.post(f"/api/v1/accounts/{s.username}/quota/topup", headers=AUTH,
                           json={"quota_mb": 1}), 422)
    assert "بلا سقف" in err["message"]
    after = _get(s.username)
    assert not after.quota_limit_enabled and int(after.combined_quota_mb or 0) == 0


# ─────────────── 11. plans: delete in use + names ───────────────

def test_delete_plan_in_use_is_409_api_and_web(client):
    csrf = _web_login(client)
    pid = _plan()
    _sub(plan_id=pid)
    err = _err(client.delete(f"/api/v1/profiles/{pid}", headers=AUTH), 409, "plan_in_use")
    assert err["details"]["subscribers"] == 1
    client.post(f"/admin/radius/plans/{pid}/delete", data={"_csrf_token": csrf})
    assert _count("SELECT COUNT(*) FROM access_plans WHERE id=? AND deleted_at IS NULL", pid) == 1
    free = _plan()
    _data(client.delete(f"/api/v1/profiles/{free}", headers=AUTH))


def test_plan_names_duplicate_is_422_and_archived_name_is_reusable(client):
    body = {"name": "st_dup", "plan_type": "time", "price": 5,
            "speed_down_kbps": 1000, "speed_up_kbps": 1000}
    first = _data(client.post("/api/v1/profiles", headers=AUTH, json=body), 201)
    err = _err(client.post("/api/v1/profiles", headers=AUTH, json=body), 422)
    assert "مستخدم مسبقًا" in err["message"]
    _err(client.post("/api/v1/profiles", headers=AUTH, json=dict(body, name=" ST_DUP ")), 422)
    _data(client.delete(f"/api/v1/profiles/{first['id']}", headers=AUTH))
    again = _data(client.post("/api/v1/profiles", headers=AUTH, json=body), 201)
    assert again["id"] != first["id"] and again["name"] == "st_dup"
    archived = _db().execute("SELECT name FROM access_plans WHERE id=?",
                             (first["id"],)).fetchone()[0]
    assert archived.startswith("st_dup (مؤرشفة")


# ─────────────── 12. paid = from the balance ───────────────

def test_paid_extend_with_insufficient_balance_is_refused(client):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid, balance=1)
    err = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH, json={
        "minutes": 1440, "charge_mode": "paid", "amount": 5}), 422)
    assert "لا يكفي" in err["message"]
    client.post(f"/admin/radius/users/{s.username}/extend", data={
        "_csrf_token": csrf, "minutes": "1440", "charge_mode": "paid", "amount": "5"})
    after = _get(s.username)
    assert float(after.balance) == 1.0 and after.expire_at == EXPIRE
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH, json={
        "minutes": 1440, "charge_mode": "debt", "amount": 5}))
    assert float(_get(s.username).balance) == -4.0


# ─────────────── 13. currency ───────────────

def test_change_plan_debt_is_in_the_wallet_currency(client):
    cheap = _plan(price=5, days=1)
    pricey = _plan(price=6, days=1, currency="USD")
    s = _sub(plan_id=cheap, expire_at=datetime.utcnow() + timedelta(days=10))
    d = _data(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                          json={"plan_id": pricey, "policy": "higher_debt"}))
    assert d["debt_amount"] > 0
    cur = _db().execute("SELECT currency FROM accounting_ledger_entries WHERE username=? "
                        "AND source_type='subscriber_plan_change'", (s.username,)).fetchone()[0]
    from app.radius.core.system_config import default_currency
    assert cur == default_currency()


def test_unknown_currency_is_refused(client):
    pid = _plan(currency="USD")
    s = _sub(plan_id=pid)
    _err(client.post("/api/v1/payments", headers=AUTH, json={
        "username": s.username, "amount": 5, "currency": "XYZ"}), 422)
    d = _data(client.post("/api/v1/payments", headers=AUTH, json={
        "username": s.username, "amount": 5}), 201)
    from app.radius.core.system_config import default_currency
    assert d["payment"]["currency"] == default_currency()


# ─────────────── 14. report totals / paging ───────────────

def test_payments_report_and_360_cover_every_row(client):
    now = datetime.utcnow().isoformat() + "Z"
    for i in range(205):
        _db().execute(
            "INSERT INTO accounting_ledger_entries(tenant_id, entry_type, direction, amount, "
            "currency, subscriber_id, username, status, created_at) "
            "VALUES(1,'payment','credit',1,'ILS',NULL,?, 'posted', ?)", (f"payer{i}", now))
    d = _data(client.get("/api/v1/reports/payments", headers=AUTH))
    assert d["count"] == 205 and d["total_count"] == 205 and d["totals"]["total"] == 205.0
    page = _data(client.get("/api/v1/reports/payments?limit=50&offset=200", headers=AUTH))
    assert page["count"] == 5 and page["has_more"] is False
    pid = _plan()
    s = _sub(plan_id=pid)
    for _ in range(60):
        _db().execute(
            "INSERT INTO payment_transactions(tenant_id, subscriber_id, username, amount, "
            "currency, method, status, created_at) VALUES(1,?,?,1,'ILS','cash','posted',?)",
            (s.id, s.username, now))
    d = _data(client.get(f"/api/v1/accounts/{s.username}/360", headers=AUTH))
    fin = d.get("financial") or d.get("data", {}).get("financial")
    assert fin["total_paid"] == 60.0


# ─────────────── 15. revenue + local day + events ───────────────

def test_revenue_endpoint_reads_the_payments(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    _data(client.post("/api/v1/payments", headers=AUTH,
                      json={"username": s.username, "amount": 12.5}), 201)
    d = _data(client.get("/api/v1/finance/revenue", headers=AUTH))
    pays = [i for i in d["items"] if i["source_type"] == "subscriber_payment"]
    assert pays and pays[0]["collected_amount"] == 12.5 and pays[0]["status"] == "posted"
    assert d["totals"]["collected"] == 12.5


def test_daily_sales_use_the_local_day(client):
    # 22:30 UTC on 2026-09-27 is 01:30 on 2026-09-28 in the panel zone (+3).
    _db().execute(
        "INSERT INTO accounting_ledger_entries(tenant_id, entry_type, direction, amount, "
        "currency, username, status, created_at) VALUES(1,'payment','credit',7,'ILS','night',"
        "'posted','2026-09-27T22:30:00Z')")
    rows = _data(client.get("/api/v1/reports/sales/daily", headers=AUTH))["items"]
    assert {r["period"]: r["total"] for r in rows}.get("2026-09-28") == 7.0


def test_events_to_filter_includes_the_whole_day(app):
    from app.radius.services.events_risk_center import EventsRiskCenterService
    _db().execute(
        "INSERT INTO business_events(tenant_id, category, severity, event_key, message, "
        "actor_type, created_at) VALUES(1,'financial','info','t.evt','x','system',"
        "'2026-09-28T10:00:00Z')")
    svc = EventsRiskCenterService(tenant_id=1)
    got = svc.list_events(date_from="2026-09-28", date_to="2026-09-28")
    assert any(e.get("event_key") == "t.evt" for e in got)


# ─────────────── 16. distributors ───────────────

def test_distributor_credit_limit_and_disabled_are_enforced(client):
    d = _data(client.post("/api/v1/distributors", headers=AUTH,
                          json={"name": "d_" + uuid4().hex[:6], "credit_limit": 100}), 201)
    did = d["distributor"]["id"]
    _data(client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                      json={"amount": 80, "direction": "debit"}), 201)
    err = _err(client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                           json={"amount": 50, "direction": "debit"}), 422)
    assert "سقف" in err["message"]
    debt = _db().execute("SELECT debt_balance FROM distributors WHERE id=?", (did,)).fetchone()[0]
    assert debt == 80.0
    _db().execute("UPDATE distributors SET status='disabled' WHERE id=?", (did,))
    _err(client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                     json={"amount": 1, "direction": "credit"}), 409)
    _db().execute("INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, created_at) "
                  "VALUES(1,'B-T-1',?,1,?)", (_plan(), datetime.utcnow().isoformat()))
    bid = _db().execute("SELECT id FROM card_batches WHERE batch_code='B-T-1'").fetchone()[0]
    _err(client.post(f"/api/v1/distributors/{did}/assign-batch", headers=AUTH,
                     json={"batch_id": bid}), 409)


# ─────────────── 17. negative amounts ───────────────

def test_negative_amounts_are_refused(client):
    pid = _plan()
    s = _sub(plan_id=pid, balance=50)
    _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH, json={
        "minutes": 60, "charge_mode": "paid", "amount": -5}), 422)
    assert float(_get(s.username).balance) == 50.0 and _get(s.username).expire_at == EXPIRE
    base = {"plan_type": "time", "speed_down_kbps": 1000, "speed_up_kbps": 1000}
    _err(client.post("/api/v1/profiles", headers=AUTH,
                     json=dict(base, name="neg1", price=-5)), 422)
    _err(client.post("/api/v1/profiles", headers=AUTH,
                     json=dict(base, name="neg2", validity_days=-3)), 422)
    _err(client.post("/api/v1/profiles", headers=AUTH,
                     json=dict(base, name="neg3", duration_minutes=-60)), 422)
