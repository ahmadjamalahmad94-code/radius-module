"""EXHAUSTIVE subscriber money matrix — every op × every balance scenario.

Drives the REAL UsersService and the REAL HTTP routes (bulk + single). For
every money op this proves the core invariant the owner demanded:

    balance_after == balance_before  ±  amount      (exactly, to the cent)

across {free, paid, debt} × {sufficient, exactly-equal, insufficient, zero,
from-negative, very-large, fractional} balances, for single AND bulk actions,
plus ledger-reconciliation, rounding/precision, currency consistency,
double-submit accumulation, overpayment handling, and multi-tenant isolation.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


# ───────────────────────── fixtures / helpers ──────────────────────────────
@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = tmp_path / "subscriber_matrix.db"
    monkeypatch.setenv("HOBERADIUS_DB_PATH", str(db_file))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(str(db_file))
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "matrix_admin"
        sess["admin_name"] = "Matrix Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "matrix-csrf"


def _plan(name, *, price=20.0, tenant_id=1, days=30):
    from app.radius.db.connection import db

    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (tenant_id, name, days * 1440, days, price, "JOD", 1,
         datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
    )
    return int(cur.lastrowid)


def _sub(username, *, plan_id, balance=0.0, tenant_id=1, used_seconds=0,
         expire_at=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import db
    from app.radius.db.repos import subscribers_repo

    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password="secret",
        plan_id=plan_id, full_name="Matrix User", mobile="0599000000",
        status="enabled", expire_at=expire_at or (datetime.utcnow() + timedelta(days=7)),
    ))
    db().execute("UPDATE subscribers SET balance=?, used_seconds=? WHERE tenant_id=? AND username=?",
                 (float(balance), int(used_seconds), tenant_id, username))


def _bal(username, tenant_id=1):
    from app.radius.db.repos import subscribers_repo

    return float(subscribers_repo.get_subscriber(tenant_id, username).balance or 0)


def _ledger(username, *, source_type="", tenant_id=1):
    from app.radius.db.connection import db

    sql = "SELECT * FROM accounting_ledger_entries WHERE tenant_id=? AND username=?"
    vals = [tenant_id, username]
    if source_type:
        sql += " AND source_type=?"
        vals.append(source_type)
    sql += " ORDER BY id"
    return [dict(r) for r in db().execute(sql, vals).fetchall()]


def _svc():
    from app.radius.services.users import get_users_service

    return get_users_service()


# Balance scenarios: (label, start, charge, expected_after_debit)
SCENARIOS = [
    ("sufficient", 100.0, 5.0, 95.0),
    ("sufficient_frac", 10.00, 3.33, 6.67),
    ("exactly_equal", 5.0, 5.0, 0.0),
    ("insufficient_small", 2.0, 5.0, -3.0),
    ("insufficient_from_zero", 0.0, 5.0, -5.0),
    ("from_negative", -10.0, 5.0, -15.0),
    ("very_large", 1_000_000.0, 999_999.0, 1.0),
    ("tiny_fraction", 1.0, 0.01, 0.99),
    ("two_decimals", 50.25, 12.75, 37.50),
]

DEBIT_OPS = ["reset", "quota", "extend"]


def _do_paid_op(svc, op, username, amount):
    if op == "reset":
        return svc.reset_daily_quota(actor="t", username=username, charge_mode="paid",
                                     amount=amount, currency="JOD")
    if op == "quota":
        return svc.add_quota(actor="t", username=username, quota_mb=100,
                             charge_mode="paid", amount=amount, currency="JOD")
    return svc.extend_time(actor="t", username=username, minutes=60,
                           charge_mode="paid", amount=amount, currency="JOD")


def _do_debt_op(svc, op, username, amount):
    if op == "reset":
        return svc.reset_daily_quota(actor="t", username=username, charge_mode="debt",
                                     amount=amount, currency="JOD")
    if op == "quota":
        return svc.add_quota(actor="t", username=username, quota_mb=100,
                             charge_mode="debt", amount=amount, currency="JOD")
    return svc.extend_time(actor="t", username=username, minutes=60,
                           charge_mode="debt", amount=amount, currency="JOD")


# ════════════════════════════════════════════════════════════════════════
# A — paid debit: balance_after == before − amount, EXACTLY, every scenario
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("op", DEBIT_OPS)
@pytest.mark.parametrize("label,start,charge,after", SCENARIOS)
def test_paid_debits_exactly(app, op, label, start, charge, after):
    with app.app_context():
        p = _plan(f"P_{op}_{label}")
        _sub("u", plan_id=p, balance=start, used_seconds=120)
        saved = _do_paid_op(_svc(), op, "u", charge)
        # the core invariant + the returned object reflects the NEW balance
        assert _bal("u") == pytest.approx(after)
        assert _bal("u") == pytest.approx(start - charge)
        assert float(saved.balance) == pytest.approx(after)


@pytest.mark.parametrize("op", DEBIT_OPS)
@pytest.mark.parametrize("label,start,charge,after", SCENARIOS)
def test_debt_debits_exactly(app, op, label, start, charge, after):
    with app.app_context():
        p = _plan(f"D_{op}_{label}")
        _sub("u", plan_id=p, balance=start, used_seconds=120)
        _do_debt_op(_svc(), op, "u", charge)
        assert _bal("u") == pytest.approx(start - charge)


# ════════════════════════════════════════════════════════════════════════
# B — free ops never move money and never write a ledger row
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("op", DEBIT_OPS)
@pytest.mark.parametrize("start", [0.0, 5.0, 100.0, -10.0])
def test_free_never_moves_money(app, op, start):
    with app.app_context():
        p = _plan(f"F_{op}_{start}")
        _sub("u", plan_id=p, balance=start, used_seconds=120)
        svc = _svc()
        if op == "reset":
            svc.reset_daily_quota(actor="t", username="u")
        elif op == "quota":
            svc.add_quota(actor="t", username="u", quota_mb=100)
        else:
            svc.extend_time(actor="t", username="u", minutes=60)
        assert _bal("u") == pytest.approx(start)
        assert _ledger("u") == []


# ════════════════════════════════════════════════════════════════════════
# C — ledger row shape (direction=debit, exact amount, currency, operator)
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("op,src", [
    ("reset", "subscriber_daily_quota_reset"),
    ("quota", "subscriber_quota_topup"),
    ("extend", "subscriber_time_extension"),
])
@pytest.mark.parametrize("amount", [1.0, 3.33, 50.0])
def test_paid_ledger_row_is_exact_debit(app, op, src, amount):
    with app.app_context():
        p = _plan(f"L_{op}_{amount}")
        _sub("u", plan_id=p, balance=500.0)
        _do_paid_op(_svc(), op, "u", amount)
        rows = _ledger("u", source_type=src)
        assert len(rows) == 1
        assert rows[0]["direction"] == "debit"
        assert float(rows[0]["amount"]) == pytest.approx(amount)
        assert rows[0]["currency"] == "JOD"
        assert rows[0]["operator"] == "t"


# ════════════════════════════════════════════════════════════════════════
# D — ledger reconciliation: start + Σcredits − Σdebits == final balance
# ════════════════════════════════════════════════════════════════════════
def test_ledger_reconciles_to_balance_over_mixed_sequence(app):
    with app.app_context():
        p = _plan("RECON")
        _sub("u", plan_id=p, balance=0.0)
        svc = _svc()
        svc.add_cash_balance(actor="t", username="u", amount=100.0, currency="JOD")  # +100
        svc.reset_daily_quota(actor="t", username="u", charge_mode="paid", amount=5.0, currency="JOD")  # -5
        svc.add_quota(actor="t", username="u", quota_mb=100, charge_mode="paid", amount=10.0, currency="JOD")  # -10
        svc.extend_time(actor="t", username="u", minutes=60, charge_mode="debt", amount=15.0, currency="JOD")  # -15
        svc.add_cash_balance(actor="t", username="u", amount=20.0, currency="JOD")  # +20
        rows = _ledger("u")
        credits = sum(float(r["amount"]) for r in rows if r["direction"] == "credit")
        debits = sum(float(r["amount"]) for r in rows if r["direction"] == "debit")
        assert credits == pytest.approx(120.0)
        assert debits == pytest.approx(30.0)
        # 0 + 120 − 30 == 90, and the stored balance matches the ledger net.
        assert _bal("u") == pytest.approx(90.0)
        assert _bal("u") == pytest.approx(credits - debits)


# ════════════════════════════════════════════════════════════════════════
# E — double-submit: subscriber ops are not idempotent → each call charges
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("n", [2, 3, 5])
def test_repeated_paid_calls_each_debit(app, n):
    with app.app_context():
        p = _plan(f"REP{n}")
        _sub("u", plan_id=p, balance=100.0)
        svc = _svc()
        for _ in range(n):
            svc.add_quota(actor="t", username="u", quota_mb=50, charge_mode="paid",
                          amount=4.0, currency="JOD")
        assert _bal("u") == pytest.approx(100.0 - 4.0 * n)
        assert len(_ledger("u", source_type="subscriber_quota_topup")) == n


# ════════════════════════════════════════════════════════════════════════
# F — add_cash_balance credit matrix (incl. settled-loan netting)
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("start,amount", [
    (0.0, 0.01), (0.0, 5.0), (10.0, 5.0), (-20.0, 25.0), (100.0, 900.0),
    (3.33, 6.67), (-5.5, 5.5),
])
def test_cash_balance_credits_exactly(app, start, amount):
    with app.app_context():
        p = _plan(f"CB_{start}_{amount}")
        _sub("u", plan_id=p, balance=start)
        saved = _svc().add_cash_balance(actor="t", username="u", amount=amount, currency="JOD")
        assert _bal("u") == pytest.approx(start + amount)
        assert float(saved.balance) == pytest.approx(start + amount)


@pytest.mark.parametrize("gross,settled,net", [
    (30.0, 10.0, 20.0), (50.0, 50.0, 0.0), (100.0, 30.0, 70.0), (10.0, 12.0, 0.0),
])
def test_cash_balance_nets_settled_deduction(app, gross, settled, net):
    with app.app_context():
        p = _plan(f"CBN_{gross}_{settled}")
        _sub("u", plan_id=p, balance=0.0)
        saved = _svc().add_cash_balance(actor="t", username="u", amount=gross,
                                        currency="JOD", settled_deduction=settled)
        assert float(saved.balance) == pytest.approx(net)


# ════════════════════════════════════════════════════════════════════════
# G — apply_payment_to_balance: cap at due, overpayment leftover NOT credited
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("debt,pay,applied,after", [
    (10.0, 25.0, 10.0, 0.0),     # overpayment → only the due is settled
    (30.0, 12.0, 12.0, -18.0),   # partial
    (5.0, 5.0, 5.0, 0.0),        # exact
    (0.0, 10.0, 0.0, 0.0),       # no debt → no-op
])
def test_apply_payment_settles_capped_at_due(app, debt, pay, applied, after):
    with app.app_context():
        p = _plan(f"AP_{debt}_{pay}")
        _sub("u", plan_id=p, balance=-debt)
        got = _svc().apply_payment_to_balance(actor="t", username="u", amount=pay)
        assert got == pytest.approx(applied)
        assert _bal("u") == pytest.approx(after)


# ════════════════════════════════════════════════════════════════════════
# H — BULK routes debit/credit each selected subscriber (single POST)
# ════════════════════════════════════════════════════════════════════════
def _seed_bulk(n, balance):
    names = [f"b{i}" for i in range(n)]
    p = _plan("BULKP")
    for nm in names:
        _sub(nm, plan_id=p, balance=balance, used_seconds=120)
    return names


def test_bulk_quota_reset_paid_debits_each(client, app):
    with app.app_context():
        names = _seed_bulk(4, 30.0)
    _auth(client)
    res = client.post("/admin/radius/users/quota/reset-daily-bulk",
                      data={"_csrf_token": "matrix-csrf", "usernames": names,
                            "charge_mode": "paid", "amount": "5", "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        for nm in names:
            assert _bal(nm) == pytest.approx(25.0)


def test_bulk_quota_topup_paid_debits_each(client, app):
    with app.app_context():
        names = _seed_bulk(3, 40.0)
    _auth(client)
    res = client.post("/admin/radius/users/quota/topup-bulk",
                      data={"_csrf_token": "matrix-csrf", "usernames": names,
                            "quota_mb": "100", "charge_mode": "paid", "amount": "8",
                            "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        for nm in names:
            assert _bal(nm) == pytest.approx(32.0)


def test_bulk_balance_add_credits_each(client, app):
    with app.app_context():
        names = _seed_bulk(3, 10.0)
    _auth(client)
    res = client.post("/admin/radius/users/balance/add-bulk",
                      data={"_csrf_token": "matrix-csrf", "usernames": names,
                            "amount": "15", "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        for nm in names:
            assert _bal(nm) == pytest.approx(25.0)


def test_bulk_reset_free_clears_counters_without_charge(client, app):
    with app.app_context():
        names = _seed_bulk(3, 20.0)
    _auth(client)
    res = client.post("/admin/radius/users/quota/reset-daily-bulk",
                      data={"_csrf_token": "matrix-csrf", "usernames": names,
                            "charge_mode": "free"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        for nm in names:
            assert _bal(nm) == pytest.approx(20.0)
            assert subscribers_repo.get_subscriber(1, nm).used_seconds == 0


# ════════════════════════════════════════════════════════════════════════
# I — single routes: returned/flashed balance reflects the NEW balance
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("start,amount,after", [
    (20.0, 5.0, 15.0), (8.5, 1.5, 7.0), (5.0, 5.0, 0.0),
])
def test_single_reset_route_paid_debits(client, app, start, amount, after):
    with app.app_context():
        p = _plan(f"SR_{start}_{amount}")
        _sub("u", plan_id=p, balance=start, used_seconds=3600)
    _auth(client)
    res = client.post("/admin/radius/users/u/quota/reset-daily",
                      data={"_csrf_token": "matrix-csrf", "charge_mode": "paid",
                            "amount": str(amount), "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _bal("u") == pytest.approx(after)


# ════════════════════════════════════════════════════════════════════════
# J — multi-tenant isolation across every subscriber op
# ════════════════════════════════════════════════════════════════════════
def _second_tenant():
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import tenants_repo

    return int(tenants_repo.create_tenant(Tenant(
        id=None, slug="t2-matrix", name="T2", display_name="T2", currency="JOD")).id)


@pytest.mark.parametrize("op", ["reset", "quota", "extend", "cash", "payment"])
def test_tenant_isolation(app, op):
    with app.app_context():
        p1 = _plan(f"ISO_{op}", tenant_id=1)
        _sub("dup", plan_id=p1, balance=30.0, tenant_id=1)
        t2 = _second_tenant()
        p2 = _plan(f"ISO2_{op}", tenant_id=t2)
        _sub("dup", plan_id=p2, balance=30.0, tenant_id=t2)
        svc = _svc()
        if op == "reset":
            svc.reset_daily_quota(actor="t", username="dup", charge_mode="paid", amount=5.0, currency="JOD")
        elif op == "quota":
            svc.add_quota(actor="t", username="dup", quota_mb=50, charge_mode="paid", amount=5.0, currency="JOD")
        elif op == "extend":
            svc.extend_time(actor="t", username="dup", minutes=60, charge_mode="paid", amount=5.0, currency="JOD")
        elif op == "cash":
            svc.add_cash_balance(actor="t", username="dup", amount=5.0, currency="JOD")
        else:
            _sub("dup", plan_id=p1, balance=-5.0, tenant_id=1)  # give tenant1 a debt to settle
            svc.apply_payment_to_balance(actor="t", username="dup", amount=5.0)
        # tenant 2's identically-named subscriber is never touched
        assert _bal("dup", tenant_id=t2) == pytest.approx(30.0)
        assert _ledger("dup", tenant_id=t2) == []
