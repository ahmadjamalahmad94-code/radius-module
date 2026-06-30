"""EXHAUSTIVE wallet / credit-gate / distributor / card-margin matrix.

Drives the REAL WalletService, ManagerCreditService, OperationsService and
CardPricingService. Covers, to the cent:

  * wallet credit/debit across sufficient / exactly-equal / insufficient / zero
    / very-large, fail-closed (never negative), and transaction-ledger
    reconciliation (last after_balance == wallet balance),
  * the spend gate boundaries: wallet-only, wallet+debt split, exactly-at-cap,
    one-cent-over-cap (blocked), uncapped owner, advance loan-cap boundary,
  * charge → reverse_charge round-trips that restore the wallet and settle the
    debt back to exactly zero,
  * distributor settlement (credit/debit/pay-down-debt/overpopulation) and
    ledger rows,
  * card batch margin math: manager wallet debited by wholesale×count, revenue
    net_profit == (retail − wholesale)×count, manager-not-allowed blocked,
  * multi-tenant wallet isolation.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "wallet_matrix.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    a = create_app()
    with a.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return a


def _db():
    from app.radius.db.connection import db

    return db()


def _ws():
    from app.radius.services.business_os_finance import WalletService

    return WalletService()


def _wal_minor(owner_type, owner_id, tenant_id=1):
    r = _db().execute("SELECT balance_minor FROM wallets WHERE tenant_id=? AND owner_type=? AND owner_id=?",
                      (tenant_id, owner_type, owner_id)).fetchone()
    return int(r["balance_minor"] or 0) if r else -1


# ════════════════════════════════════════════════════════════════════════
# A — WalletService credit / debit matrix
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("amount,minor", [
    ("0.01", 1), ("1.00", 100), ("2.50", 250), ("3.33", 333),
    ("12.35", 1235), ("100.00", 10000), ("9999.99", 999999), ("0.99", 99),
])
def test_credit_exact_minor(app, amount, minor):
    with app.app_context():
        w = _ws().create_wallet(tenant_id=1, owner_type="manager", owner_id=1)
        _ws().credit(tenant_id=1, wallet_id=w["id"], amount=amount, actor_type="admin", actor_id=1)
        assert _wal_minor("manager", 1) == minor


@pytest.mark.parametrize("seed,debit,left", [
    ("100.00", "100.00", 0),      # exactly-equal → zero
    ("100.00", "0.01", 9999),
    ("100.00", "33.33", 6667),
    ("50.00", "49.99", 1),
    ("1000.00", "1.00", 99900),
])
def test_debit_exact_minor(app, seed, debit, left):
    with app.app_context():
        w = _ws().create_wallet(tenant_id=1, owner_type="manager", owner_id=2)
        _ws().credit(tenant_id=1, wallet_id=w["id"], amount=seed, actor_type="admin", actor_id=1)
        _ws().debit(tenant_id=1, wallet_id=w["id"], amount=debit, actor_type="admin", actor_id=1)
        assert _wal_minor("manager", 2) == left


@pytest.mark.parametrize("seed,debit", [
    ("0.00", "0.01"), ("5.00", "5.01"), ("10.00", "10.01"), ("0.00", "100.00"),
])
def test_debit_never_negative(app, seed, debit):
    from app.radius.services.business_os_finance import BusinessOSValidationError

    with app.app_context():
        w = _ws().create_wallet(tenant_id=1, owner_type="manager", owner_id=3)
        if float(seed) > 0:
            _ws().credit(tenant_id=1, wallet_id=w["id"], amount=seed, actor_type="admin", actor_id=1)
        before = _wal_minor("manager", 3)
        with pytest.raises(BusinessOSValidationError):
            _ws().debit(tenant_id=1, wallet_id=w["id"], amount=debit, actor_type="admin", actor_id=1)
        assert _wal_minor("manager", 3) == before  # untouched on refusal


def test_wallet_transaction_ledger_reconciles(app):
    with app.app_context():
        w = _ws().create_wallet(tenant_id=1, owner_type="manager", owner_id=4)
        seq = [("credit", "100.00"), ("debit", "30.00"), ("credit", "5.50"), ("debit", "25.50")]
        for kind, amt in seq:
            getattr(_ws(), kind)(tenant_id=1, wallet_id=w["id"], amount=amt, actor_type="admin", actor_id=1)
        rows = _db().execute(
            "SELECT transaction_type, amount_minor, after_balance_minor FROM wallet_transactions "
            "WHERE wallet_id=? ORDER BY id", (w["id"],)).fetchall()
        # replay the ledger and confirm each after_balance + final == wallet
        bal = 0
        for r in rows:
            bal += int(r["amount_minor"]) if r["transaction_type"] == "credit" else -int(r["amount_minor"])
            assert int(r["after_balance_minor"]) == bal
        assert _wal_minor("manager", 4) == bal == 5000


# ════════════════════════════════════════════════════════════════════════
# B — ManagerCreditService spend gate boundaries
# ════════════════════════════════════════════════════════════════════════
def _owner():
    from app.radius.db.repos import admins_repo

    return admins_repo.create_admin(username="own", password="x", is_super_admin=True).id


def _mgr(debt_cap=None, loan_cap=None):
    from app.radius.db.repos import admins_repo

    a = admins_repo.create_admin(username=f"m{debt_cap}{loan_cap}", password="x")
    ch = {}
    if debt_cap is not None:
        ch.update(debt_cap_enabled=True, debt_cap_minor=int(round(debt_cap * 100)))
    if loan_cap is not None:
        ch.update(loan_cap_enabled=True, loan_cap_minor=int(round(loan_cap * 100)))
    if ch:
        admins_repo.update_admin(a.id, **ch)
    return a.id


def _fund(mid, money):
    w = _ws().create_wallet(tenant_id=1, owner_type="manager", owner_id=mid)
    if money > 0:
        _ws().credit(tenant_id=1, wallet_id=w["id"], amount=f"{money:.2f}", actor_type="admin", actor_id=1)


def _credit_svc():
    from app.radius.services.manager_credit import ManagerCreditService

    return ManagerCreditService(tenant_id=1)


@pytest.mark.parametrize("wallet,cap,cost,mode,w_deduct,debt", [
    (100.0, 100.0, 5000, "wallet", 5000, 0),    # fully covered by wallet
    (100.0, 100.0, 10000, "wallet", 10000, 0),  # exactly the wallet
    (30.0, 100.0, 5000, "debt", 3000, 2000),    # split wallet + debt
    (0.0, 50.0, 5000, "debt", 0, 5000),         # exactly at debt cap
    (0.0, 50.0, 4999, "debt", 0, 4999),         # just under cap
])
def test_gate_funding_split_is_exact(app, wallet, cap, cost, mode, w_deduct, debt):
    with app.app_context():
        _owner()
        m = _mgr(debt_cap=cap)
        _fund(m, wallet)
        d = _credit_svc().evaluate(m, cost)
        assert d.ok and d.mode == mode
        assert d.wallet_deduct_minor == w_deduct
        assert d.debt_minor == debt


@pytest.mark.parametrize("cap,cost", [(50.0, 5001), (50.0, 6000), (10.0, 1001)])
def test_gate_one_cent_over_cap_blocks(app, cap, cost):
    with app.app_context():
        _owner()
        m = _mgr(debt_cap=cap)
        _fund(m, 0.0)
        d = _credit_svc().evaluate(m, cost)
        assert d.ok is False and d.mode == "blocked"


@pytest.mark.parametrize("cost", [100, 5000, 5_000_000])
def test_owner_uncapped_never_blocks(app, cost):
    with app.app_context():
        o = _owner()
        _fund(o, 0.0)
        svc = _credit_svc()
        assert svc.is_uncapped(o)
        assert svc.evaluate(o, cost).ok


def test_gate_commit_then_reverse_round_trips(app):
    with app.app_context():
        _owner()
        m = _mgr(debt_cap=100.0)
        _fund(m, 30.0)  # 3000 minor
        svc = _credit_svc()
        d = svc.evaluate(m, 5000)        # 3000 wallet + 2000 debt
        charge = svc.commit(m, d, kind="card_package", reference_type="t", reference_id=1)
        assert _wal_minor("manager", m) == 0
        assert svc.current_debt_minor(m) == 2000
        # reverse → wallet restored to 3000, debt settled to 0 (made whole)
        svc.reverse_charge(m, charge, reference_type="t", reference_id=1, actor="admin")
        assert _wal_minor("manager", m) == 3000
        assert svc.current_debt_minor(m) == 0


@pytest.mark.parametrize("loan_cap,advance,ok", [
    (50.0, 4000, True), (50.0, 5000, True), (50.0, 5001, False), (50.0, 9000, False),
])
def test_advance_respects_loan_cap_boundary(app, loan_cap, advance, ok):
    with app.app_context():
        _owner()
        m = _mgr(debt_cap=1000.0, loan_cap=loan_cap)
        _fund(m, 0.0)
        assert _credit_svc().evaluate_advance(m, advance).ok is ok


# ════════════════════════════════════════════════════════════════════════
# C — distributor settlement matrix
# ════════════════════════════════════════════════════════════════════════
def _ops():
    from app.radius.services.operations import get_operations_service

    return get_operations_service()


def _mk_dist(name):
    return int(_ops().create_distributor(tenant_id=1, actor="a",
                                         data={"name": name, "credit_limit": 100000.0})["id"])


def _dist(did):
    r = _db().execute("SELECT balance, debt_balance FROM distributors WHERE tenant_id=1 AND id=?",
                      (did,)).fetchone()
    return float(r["balance"] or 0), float(r["debt_balance"] or 0)


@pytest.mark.parametrize("amount", [0.01, 5.0, 12.5, 100.0, 9999.99])
def test_distributor_credit_then_debit_exact(app, amount):
    with app.app_context():
        did = _mk_dist(f"dx{amount}")
        _ops().settle_distributor(tenant_id=1, distributor_id=did, actor="a",
                                  data={"amount": amount, "direction": "credit"})
        assert _dist(did) == (pytest.approx(amount), pytest.approx(0.0))
        _ops().settle_distributor(tenant_id=1, distributor_id=did, actor="a",
                                  data={"amount": amount, "direction": "debit"})
        bal, debt = _dist(did)
        assert debt == pytest.approx(amount)


@pytest.mark.parametrize("debt0,credit,exp_bal,exp_debt", [
    (30.0, 30.0, 30.0, 0.0),    # credit equals debt → debt cleared, full credit to balance
    (30.0, 50.0, 50.0, 0.0),    # overpayment → leftover stays in balance
    (30.0, 10.0, 10.0, 20.0),   # partial pay-down
])
def test_distributor_credit_clears_debt(app, debt0, credit, exp_bal, exp_debt):
    with app.app_context():
        did = _mk_dist(f"dd{debt0}{credit}")
        ops = _ops()
        ops.settle_distributor(tenant_id=1, distributor_id=did, actor="a",
                               data={"amount": debt0, "direction": "debit"})
        ops.settle_distributor(tenant_id=1, distributor_id=did, actor="a",
                               data={"amount": credit, "direction": "credit"})
        bal, debt = _dist(did)
        assert bal == pytest.approx(exp_bal)
        assert debt == pytest.approx(exp_debt)


# ════════════════════════════════════════════════════════════════════════
# D — card batch margin math (wholesale × count, net profit)
# ════════════════════════════════════════════════════════════════════════
def _market():
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService

    return CardUsersMarketplaceService(tenant_id=1)


def _pricing():
    from app.radius.services.card_pricing import CardPricingService

    return CardPricingService(tenant_id=1)


def _market_plan(price=10.0):
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, price, "
        "currency, created_at, updated_at) VALUES(1,'BP',600,2,?, 'JOD', datetime('now'), datetime('now'))",
        (price,))
    return int(cur.lastrowid)


@pytest.mark.parametrize("retail,wholesale,count", [
    (10.0, 6.0, 4), (5.0, 3.0, 10), (20.0, 12.5, 2), (1.0, 0.5, 100),
])
def test_costed_batch_charges_wholesale_and_records_margin(app, retail, wholesale, count):
    with app.app_context():
        _owner()  # id 1 = primary owner; manager will be id 2 (capped)
        from app.radius.db.repos import admins_repo
        mid = admins_repo.create_admin(username="batchmgr", password="x").id
        _fund(mid, 100000.0)  # plenty so the whole cost comes from the wallet
        plan = _market_plan(retail)
        pkg = _market().create_package(name="BP", plan_id=plan, duration_minutes=600,
                                       speed_down_kbps=2048, speed_up_kbps=512, price=f"{retail:.2f}")
        _pricing().set_package_pricing(package_id=pkg["id"], retail_price=f"{retail:.2f}",
                                       wholesale_price=f"{wholesale:.2f}", allowed_manager_ids=[mid])
        before = _wal_minor("manager", mid)
        result = _pricing().create_costed_batch(package_id=pkg["id"], count=count,
                                                responsible_manager_id=mid, creator_type="admin",
                                                creator_id=1, actor="admin")
        total_wholesale_minor = int(round(wholesale * 100)) * count
        # manager wallet debited by exactly wholesale × count
        assert before - _wal_minor("manager", mid) == total_wholesale_minor
        # revenue record net profit == (retail − wholesale) × count
        batch_id = result["batch"]["id"] if "batch" in result else result.get("batch_id")
        rev = _db().execute(
            "SELECT net_profit_minor, retail_price_minor, wholesale_cost_minor FROM revenue_records "
            "WHERE source_type='card_batch' AND source_id=?", (batch_id,)).fetchone()
        exp_net = (int(round(retail * 100)) - int(round(wholesale * 100))) * count
        assert int(rev["net_profit_minor"]) == exp_net


def test_costed_batch_blocks_disallowed_manager(app):
    from app.radius.services.card_pricing import CardPricingError

    with app.app_context():
        _owner()
        from app.radius.db.repos import admins_repo
        allowed = admins_repo.create_admin(username="ok", password="x").id
        other = admins_repo.create_admin(username="no", password="x").id
        _fund(other, 100000.0)
        plan = _market_plan(10.0)
        pkg = _market().create_package(name="BP", plan_id=plan, duration_minutes=600,
                                       speed_down_kbps=2048, speed_up_kbps=512, price="10.00")
        _pricing().set_package_pricing(package_id=pkg["id"], retail_price="10.00",
                                       wholesale_price="6.00", allowed_manager_ids=[allowed])
        with pytest.raises(CardPricingError):
            _pricing().create_costed_batch(package_id=pkg["id"], count=1,
                                           responsible_manager_id=other, creator_type="admin",
                                           creator_id=1, actor="admin")


# ════════════════════════════════════════════════════════════════════════
# E — card-user marketplace purchase: exact debit + no double-spend
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("price", ["2.00", "5.00", "10.00"])
def test_marketplace_purchase_debits_exact_and_writes_card_sale(app, price):
    with app.app_context():
        svc = _market()
        plan = _market_plan(float(price))
        user = svc.create_card_user(display_name="Buyer", mobile="0590000000")
        pkg = svc.create_package(name="P", plan_id=plan, duration_minutes=480,
                                 speed_down_kbps=2048, speed_up_kbps=512, price=price)
        svc.recharge_wallet(card_user_id=user["id"], amount=price, actor="qa")
        purchase = svc.purchase_package(card_user_id=user["id"], package_id=pkg["id"], actor="qa")
        assert _wal_minor("card_user", user["id"]) == 0
        led = _db().execute("SELECT entry_type, amount_minor FROM ledger_entries "
                            "WHERE reference_type='card_user_purchase' AND reference_id=?",
                            (purchase["id"],)).fetchone()
        assert led and led["entry_type"] == "card_sale"
        assert int(led["amount_minor"]) == int(round(float(price) * 100))


def test_marketplace_one_recharge_funds_one_purchase_only(app):
    # A single recharge cannot be spent twice — the second purchase is blocked
    # because the wallet is empty (no charge-without-funds).
    from app.radius.services.card_users_marketplace import CardMarketplaceError

    with app.app_context():
        svc = _market()
        plan = _market_plan(5.0)
        user = svc.create_card_user(display_name="Buyer", mobile="0590000001")
        pkg = svc.create_package(name="P5", plan_id=plan, duration_minutes=480,
                                 speed_down_kbps=2048, speed_up_kbps=512, price="5.00")
        svc.recharge_wallet(card_user_id=user["id"], amount="5.00", actor="qa")
        svc.purchase_package(card_user_id=user["id"], package_id=pkg["id"], actor="qa")
        assert _wal_minor("card_user", user["id"]) == 0
        with pytest.raises(CardMarketplaceError):
            svc.purchase_package(card_user_id=user["id"], package_id=pkg["id"], actor="qa")
        assert _wal_minor("card_user", user["id"]) == 0  # still zero — no negative spend


# ════════════════════════════════════════════════════════════════════════
# F — multi-tenant wallet isolation
# ════════════════════════════════════════════════════════════════════════
def test_wallet_isolation_across_tenants(app):
    with app.app_context():
        from app.radius.core.tenant import Tenant
        from app.radius.db.repos import tenants_repo

        t2 = int(tenants_repo.create_tenant(Tenant(id=None, slug="t2w", name="T2",
                                                   display_name="T2", currency="JOD")).id)
        w1 = _ws().create_wallet(tenant_id=1, owner_type="manager", owner_id=9)
        w2 = _ws().create_wallet(tenant_id=t2, owner_type="manager", owner_id=9)
        _ws().credit(tenant_id=1, wallet_id=w1["id"], amount="50.00", actor_type="admin", actor_id=1)
        # tenant 2's wallet for the same owner_id is untouched
        assert _wal_minor("manager", 9, tenant_id=1) == 5000
        assert _wal_minor("manager", 9, tenant_id=t2) == 0
