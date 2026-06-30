"""Money-movement audit across the NON-subscriber domains: manager wallets,
the ManagerCreditService spend gate, distributor settlement, the card-user
marketplace, and payment-collection.

Every test drives the REAL services (no mocks of the unit under test) and
asserts the canonical invariants:

  * a debit decrements the balance by exactly the charged amount and writes a
    transaction/ledger row; a credit increments it by exactly the amount,
  * wallets are fail-closed (a debit can never drive the balance negative),
  * the spend gate funds from the wallet first, then debt within the cap, and
    blocks past the cap,
  * distributor settlement moves distributors.balance / debt_balance and writes
    a ledger row,
  * marketplace purchase debits the buyer wallet + writes a card_sale ledger row
    (and blocks on insufficient balance — no charge without effect),
  * payment-collection approval writes exactly one ledger row and is idempotent;
    a non-paid request never writes one (no effect without the money).
"""
from __future__ import annotations

import os

import pytest


# ───────────────────────── fixtures / helpers ──────────────────────────────
@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "cross_domain_money.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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
    flask_app.config["_TEST_DB"] = db_file
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _db():
    from app.radius.db.connection import db

    return db()


def _wallet_service():
    from app.radius.services.business_os_finance import WalletService

    return WalletService()


def _wallet_balance_minor(owner_type: str, owner_id: int, tenant_id: int = 1) -> int:
    row = _db().execute(
        "SELECT balance_minor FROM wallets WHERE tenant_id=? AND owner_type=? AND owner_id=?",
        (tenant_id, owner_type, owner_id),
    ).fetchone()
    return int(row["balance_minor"] or 0) if row else -1


def _owner_admin():
    """Create admin id #1 — the primary owner (uncapped provider)."""
    from app.radius.db.repos import admins_repo

    return admins_repo.create_admin(username="owner1", password="x",
                                    is_super_admin=True).id


def _manager(*, debt_cap=None, loan_cap=None):
    from app.radius.db.repos import admins_repo

    a = admins_repo.create_admin(username=f"mgr_{debt_cap}_{loan_cap}", password="x")
    changes = {}
    if debt_cap is not None:
        changes["debt_cap_enabled"] = True
        changes["debt_cap_minor"] = int(round(float(debt_cap) * 100))
    if loan_cap is not None:
        changes["loan_cap_enabled"] = True
        changes["loan_cap_minor"] = int(round(float(loan_cap) * 100))
    if changes:
        admins_repo.update_admin(a.id, **changes)
    return a.id


def _fund_manager(manager_id: int, money: float):
    svc = _wallet_service()
    w = svc.create_wallet(tenant_id=1, owner_type="manager", owner_id=int(manager_id))
    if money > 0:
        svc.credit(tenant_id=1, wallet_id=int(w["id"]), amount=f"{money:.2f}",
                   actor_type="admin", actor_id=1, reference_type="test_fund")
    return int(w["id"])


# ════════════════════════════════════════════════════════════════════════
# GROUP A — WalletService credit / debit (the canonical wallet path)
# ════════════════════════════════════════════════════════════════════════
WALLET_AMOUNTS = ["1.00", "2.50", "5.00", "12.35", "100.00", "999.99"]


@pytest.mark.parametrize("amount", WALLET_AMOUNTS)
def test_wallet_credit_increments_balance_minor(app, amount):
    with app.app_context():
        svc = _wallet_service()
        w = svc.create_wallet(tenant_id=1, owner_type="manager", owner_id=5)
        svc.credit(tenant_id=1, wallet_id=w["id"], amount=amount,
                   actor_type="admin", actor_id=1, reference_type="manual")
        expected = int(round(float(amount) * 100))
        assert _wallet_balance_minor("manager", 5) == expected


@pytest.mark.parametrize("amount", WALLET_AMOUNTS)
def test_wallet_debit_decrements_balance_minor(app, amount):
    with app.app_context():
        svc = _wallet_service()
        w = svc.create_wallet(tenant_id=1, owner_type="manager", owner_id=6)
        svc.credit(tenant_id=1, wallet_id=w["id"], amount="1000.00",
                   actor_type="admin", actor_id=1, reference_type="seed")
        svc.debit(tenant_id=1, wallet_id=w["id"], amount=amount,
                  actor_type="admin", actor_id=1, reference_type="spend")
        expected = 100000 - int(round(float(amount) * 100))
        assert _wallet_balance_minor("manager", 6) == expected


@pytest.mark.parametrize("amount", ["0.01", "1.00", "50.00", "9999.00"])
def test_wallet_debit_cannot_go_negative(app, amount):
    from app.radius.services.business_os_finance import BusinessOSValidationError

    with app.app_context():
        svc = _wallet_service()
        w = svc.create_wallet(tenant_id=1, owner_type="manager", owner_id=7)
        # wallet balance is 0 → any debit must be refused, balance untouched
        with pytest.raises(BusinessOSValidationError):
            svc.debit(tenant_id=1, wallet_id=w["id"], amount=amount,
                      actor_type="admin", actor_id=1)
        assert _wallet_balance_minor("manager", 7) == 0


def test_wallet_credit_then_debit_nets_correctly(app):
    with app.app_context():
        svc = _wallet_service()
        w = svc.create_wallet(tenant_id=1, owner_type="distributor", owner_id=3)
        svc.credit(tenant_id=1, wallet_id=w["id"], amount="40.00",
                   actor_type="admin", actor_id=1, reference_type="seed")
        svc.debit(tenant_id=1, wallet_id=w["id"], amount="15.50",
                  actor_type="admin", actor_id=1, reference_type="spend")
        assert _wallet_balance_minor("distributor", 3) == 2450


def test_wallet_transactions_are_recorded(app):
    with app.app_context():
        svc = _wallet_service()
        w = svc.create_wallet(tenant_id=1, owner_type="manager", owner_id=8)
        svc.credit(tenant_id=1, wallet_id=w["id"], amount="10.00",
                   actor_type="admin", actor_id=1, reference_type="seed")
        svc.debit(tenant_id=1, wallet_id=w["id"], amount="4.00",
                  actor_type="admin", actor_id=1, reference_type="spend")
        rows = _db().execute(
            "SELECT transaction_type, amount_minor, after_balance_minor "
            "FROM wallet_transactions WHERE wallet_id=? ORDER BY id", (w["id"],),
        ).fetchall()
        assert [r["transaction_type"] for r in rows] == ["credit", "debit"]
        assert rows[-1]["after_balance_minor"] == 600


@pytest.mark.parametrize("amount", ["0.00", "-1.00", "-50.00"])
def test_wallet_credit_rejects_non_positive(app, amount):
    from app.radius.services.business_os_finance import BusinessOSValidationError

    with app.app_context():
        svc = _wallet_service()
        w = svc.create_wallet(tenant_id=1, owner_type="manager", owner_id=9)
        with pytest.raises(BusinessOSValidationError):
            svc.credit(tenant_id=1, wallet_id=w["id"], amount=amount,
                       actor_type="admin", actor_id=1)
        assert _wallet_balance_minor("manager", 9) == 0


# ════════════════════════════════════════════════════════════════════════
# GROUP B — ManagerCreditService spend gate (wallet → debt cap → block)
# ════════════════════════════════════════════════════════════════════════
def _credit_svc():
    from app.radius.services.manager_credit import ManagerCreditService

    return ManagerCreditService(tenant_id=1)


@pytest.mark.parametrize("cost_minor", [100, 500, 1500, 5000])
def test_gate_funds_from_wallet_when_sufficient(app, cost_minor):
    with app.app_context():
        _owner_admin()
        m = _manager(debt_cap=100.0)
        _fund_manager(m, 100.0)  # 10000 minor
        svc = _credit_svc()
        decision = svc.evaluate(m, cost_minor)
        assert decision.ok and decision.mode == "wallet"
        svc.commit(m, decision, kind="card_package", reference_type="t", reference_id=1)
        assert _wallet_balance_minor("manager", m) == 10000 - cost_minor
        assert svc.current_debt_minor(m) == 0


@pytest.mark.parametrize("cost_minor", [3000, 5000, 10000])
def test_gate_uses_debt_within_cap(app, cost_minor):
    with app.app_context():
        _owner_admin()
        m = _manager(debt_cap=200.0)   # cap 20000 minor
        _fund_manager(m, 0.0)          # empty wallet → whole cost is debt
        svc = _credit_svc()
        decision = svc.evaluate(m, cost_minor)
        assert decision.ok and decision.mode == "debt"
        svc.commit(m, decision, kind="card_package", reference_type="t", reference_id=2)
        assert svc.current_debt_minor(m) == cost_minor
        # ledger row recorded in manager_credit_ledger
        row = _db().execute(
            "SELECT * FROM manager_credit_ledger WHERE manager_id=? AND kind='debt'", (m,),
        ).fetchone()
        assert row and int(row["amount_minor"]) == cost_minor


@pytest.mark.parametrize("cost_minor", [25000, 50000, 999999])
def test_gate_blocks_past_debt_cap(app, cost_minor):
    with app.app_context():
        _owner_admin()
        m = _manager(debt_cap=200.0)   # cap 20000 minor
        _fund_manager(m, 0.0)
        decision = _credit_svc().evaluate(m, cost_minor)
        assert decision.ok is False and decision.mode == "blocked"


def test_gate_blocks_when_no_cap_configured(app):
    # zero-trust: a manager with no debt cap and an empty wallet cannot spend.
    with app.app_context():
        _owner_admin()
        m = _manager()  # no caps
        _fund_manager(m, 0.0)
        decision = _credit_svc().evaluate(m, 500)
        assert decision.ok is False


@pytest.mark.parametrize("cost_minor", [5000, 50000, 1000000])
def test_owner_is_uncapped(app, cost_minor):
    with app.app_context():
        owner = _owner_admin()  # id #1 = primary owner
        _fund_manager(owner, 0.0)
        svc = _credit_svc()
        assert svc.is_uncapped(owner) is True
        decision = svc.evaluate(owner, cost_minor)
        assert decision.ok and decision.mode == "debt"  # funded as uncapped debt


def test_gate_partial_wallet_then_debt(app):
    with app.app_context():
        _owner_admin()
        m = _manager(debt_cap=100.0)
        _fund_manager(m, 30.0)  # 3000 minor wallet
        svc = _credit_svc()
        decision = svc.evaluate(m, 5000)  # 3000 wallet + 2000 debt
        assert decision.ok and decision.mode == "debt"
        assert decision.wallet_deduct_minor == 3000
        assert decision.debt_minor == 2000
        svc.commit(m, decision, kind="card_package", reference_type="t", reference_id=3)
        assert _wallet_balance_minor("manager", m) == 0
        assert svc.current_debt_minor(m) == 2000


def test_advance_gate_respects_loan_cap(app):
    with app.app_context():
        _owner_admin()
        m = _manager(debt_cap=1000.0, loan_cap=50.0)  # loan cap 5000 minor
        _fund_manager(m, 0.0)
        svc = _credit_svc()
        ok_decision = svc.evaluate_advance(m, 4000)
        assert ok_decision.ok
        blocked = svc.evaluate_advance(m, 9000)  # exceeds loan cap
        assert blocked.ok is False


# ════════════════════════════════════════════════════════════════════════
# GROUP C — distributor settlement
# ════════════════════════════════════════════════════════════════════════
def _ops():
    from app.radius.services.operations import get_operations_service

    return get_operations_service()


def _make_distributor(name="dist1"):
    d = _ops().create_distributor(tenant_id=1, actor="admin",
                                  data={"name": name, "credit_limit": 1000.0})
    return int(d["id"])


def _distributor(did):
    row = _db().execute(
        "SELECT balance, debt_balance FROM distributors WHERE tenant_id=1 AND id=?", (did,),
    ).fetchone()
    return float(row["balance"] or 0), float(row["debt_balance"] or 0)


@pytest.mark.parametrize("amount", [5.0, 12.5, 50.0, 100.0, 999.0])
def test_distributor_credit_increases_balance(app, amount):
    with app.app_context():
        did = _make_distributor(f"dc{amount}")
        _ops().settle_distributor(tenant_id=1, distributor_id=did, actor="admin",
                                  data={"amount": amount, "direction": "credit"})
        bal, debt = _distributor(did)
        assert bal == pytest.approx(amount)
        assert debt == pytest.approx(0.0)


@pytest.mark.parametrize("amount", [5.0, 12.5, 50.0, 100.0, 999.0])
def test_distributor_debit_increases_debt(app, amount):
    with app.app_context():
        did = _make_distributor(f"dd{amount}")
        _ops().settle_distributor(tenant_id=1, distributor_id=did, actor="admin",
                                  data={"amount": amount, "direction": "debit"})
        bal, debt = _distributor(did)
        assert debt == pytest.approx(amount)


def test_distributor_credit_pays_down_debt_first(app):
    with app.app_context():
        did = _make_distributor("dpd")
        ops = _ops()
        ops.settle_distributor(tenant_id=1, distributor_id=did, actor="admin",
                               data={"amount": 30.0, "direction": "debit"})
        ops.settle_distributor(tenant_id=1, distributor_id=did, actor="admin",
                               data={"amount": 50.0, "direction": "credit"})
        bal, debt = _distributor(did)
        # credit adds the full amount to balance AND clears outstanding debt
        assert debt == pytest.approx(0.0)
        assert bal == pytest.approx(50.0)


def test_distributor_settlement_writes_ledger_rows(app):
    with app.app_context():
        did = _make_distributor("dl")
        ops = _ops()
        ops.settle_distributor(tenant_id=1, distributor_id=did, actor="admin",
                               data={"amount": 50.0, "direction": "credit"})
        ops.settle_distributor(tenant_id=1, distributor_id=did, actor="admin",
                               data={"amount": 25.0, "direction": "debit"})
        rows = _db().execute(
            "SELECT direction, amount FROM distributor_ledger_entries "
            "WHERE tenant_id=1 AND distributor_id=? ORDER BY id", (did,),
        ).fetchall()
        assert [(r["direction"], float(r["amount"])) for r in rows] == [
            ("credit", 50.0), ("debit", 25.0),
        ]


@pytest.mark.parametrize("direction", ["sideways", "kredit", "xfer"])
def test_distributor_settle_rejects_bad_direction(app, direction):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        did = _make_distributor(f"db{direction or 'empty'}")
        with pytest.raises(RadiusValidationError):
            _ops().settle_distributor(tenant_id=1, distributor_id=did, actor="admin",
                                      data={"amount": 10.0, "direction": direction})


@pytest.mark.parametrize("amount", [0.0, -5.0])
def test_distributor_settle_rejects_non_positive_amount(app, amount):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        did = _make_distributor(f"dn{amount}")
        with pytest.raises(RadiusValidationError):
            _ops().settle_distributor(tenant_id=1, distributor_id=did, actor="admin",
                                      data={"amount": amount, "direction": "credit"})


# ════════════════════════════════════════════════════════════════════════
# GROUP D — card-user marketplace (recharge / purchase)
# ════════════════════════════════════════════════════════════════════════
def _market_service():
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService

    return CardUsersMarketplaceService(tenant_id=1)


def _seed_market_plan(price=5.0):
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, created_at, updated_at) "
        "VALUES(1,'Mk',480,1,?, 'JOD', datetime('now'), datetime('now'))",
        (price,),
    )
    return int(cur.lastrowid)


@pytest.mark.parametrize("amount", ["1.00", "5.00", "10.00", "25.50", "100.00"])
def test_card_user_recharge_credits_wallet(app, amount):
    with app.app_context():
        svc = _market_service()
        user = svc.create_card_user(display_name="Buyer", mobile="0590000000")
        svc.recharge_wallet(card_user_id=user["id"], amount=amount, actor="qa")
        expected = int(round(float(amount) * 100))
        assert _wallet_balance_minor("card_user", user["id"]) == expected


@pytest.mark.parametrize("price", [2.0, 5.0, 10.0])
def test_marketplace_purchase_debits_wallet_and_writes_ledger(app, price):
    with app.app_context():
        svc = _market_service()
        plan = _seed_market_plan(price)
        user = svc.create_card_user(display_name="Buyer", mobile="0591234567")
        package = svc.create_package(name=f"P{price}", plan_id=plan,
                                     duration_minutes=480, speed_down_kbps=2048,
                                     speed_up_kbps=512, price=f"{price:.2f}")
        svc.recharge_wallet(card_user_id=user["id"], amount=f"{price:.2f}", actor="qa")
        purchase = svc.purchase_package(card_user_id=user["id"],
                                        package_id=package["id"], actor="qa")
        # wallet fully consumed
        assert _wallet_balance_minor("card_user", user["id"]) == 0
        ledger = _db().execute(
            "SELECT * FROM ledger_entries WHERE reference_type='card_user_purchase' "
            "AND reference_id=?", (purchase["id"],),
        ).fetchone()
        assert ledger and ledger["entry_type"] == "card_sale"
        assert int(ledger["amount_minor"]) == int(round(price * 100))


def test_marketplace_purchase_blocks_insufficient_balance_no_charge(app):
    from app.radius.services.card_users_marketplace import CardMarketplaceError

    with app.app_context():
        svc = _market_service()
        plan = _seed_market_plan(5.0)
        user = svc.create_card_user(display_name="Broke", mobile="0599999999")
        package = svc.create_package(name="P5", plan_id=plan, duration_minutes=480,
                                     speed_down_kbps=2048, speed_up_kbps=512,
                                     price="5.00")
        svc.recharge_wallet(card_user_id=user["id"], amount="2.00", actor="qa")
        with pytest.raises(CardMarketplaceError):
            svc.purchase_package(card_user_id=user["id"], package_id=package["id"],
                                 actor="qa")
        # no charge happened — balance intact, no ledger row
        assert _wallet_balance_minor("card_user", user["id"]) == 200
        n = _db().execute(
            "SELECT COUNT(*) n FROM ledger_entries WHERE reference_type='card_user_purchase'",
        ).fetchone()["n"]
        assert n == 0


def test_marketplace_purchase_records_revenue(app):
    with app.app_context():
        svc = _market_service()
        plan = _seed_market_plan(5.0)
        user = svc.create_card_user(display_name="Buyer", mobile="0591111111")
        package = svc.create_package(name="P5", plan_id=plan, duration_minutes=480,
                                     speed_down_kbps=2048, speed_up_kbps=512,
                                     price="5.00")
        svc.recharge_wallet(card_user_id=user["id"], amount="5.00", actor="qa")
        purchase = svc.purchase_package(card_user_id=user["id"],
                                        package_id=package["id"], actor="qa")
        rev = _db().execute(
            "SELECT collected_amount_minor FROM revenue_records "
            "WHERE source_type='card_user_purchase' AND source_id=?", (purchase["id"],),
        ).fetchone()
        assert rev and int(rev["collected_amount_minor"]) == 500


# ════════════════════════════════════════════════════════════════════════
# GROUP E — payment-collection (ledger written once; reject = no money)
# ════════════════════════════════════════════════════════════════════════
def _payment_repos():
    from app.radius.db.repos.payments_repo import (
        PaymentCollectionLedgerRepository,
        PaymentRequestRepository,
    )

    return PaymentRequestRepository(), PaymentCollectionLedgerRepository()


@pytest.mark.parametrize("amount,currency", [
    (25.0, "ILS"), (10.0, "USD"), (5.5, "JOD"), (100.0, "ILS"),
])
def test_payment_collection_approve_writes_one_credit_ledger(app, amount, currency):
    with app.app_context():
        req_repo, ledger_repo = _payment_repos()
        req = req_repo.create(tenant_id=1, payer_type="subscriber", purpose="card_purchase",
                              amount=amount, currency=currency, provider="manual_wallet",
                              receiver_wallet="0599000000")
        req_repo.update_status(1, req["id"], "paid")
        ledger = ledger_repo.apply_paid_request(tenant_id=1, request_id=req["id"], actor="admin")
        assert ledger["entry_type"] == "payment"
        assert ledger["direction"] == "credit"
        assert float(ledger["amount"]) == pytest.approx(amount)
        n = _db().execute(
            "SELECT COUNT(*) n FROM accounting_ledger_entries "
            "WHERE source_type='payment_collection_request' AND source_id=?", (req["id"],),
        ).fetchone()["n"]
        assert n == 1


@pytest.mark.parametrize("times", [2, 3, 5])
def test_payment_collection_apply_is_idempotent(app, times):
    with app.app_context():
        req_repo, ledger_repo = _payment_repos()
        req = req_repo.create(tenant_id=1, payer_type="subscriber", purpose="monthly_subscription",
                              amount=15.0, currency="ILS", provider="manual_wallet",
                              receiver_wallet="0599000000")
        req_repo.update_status(1, req["id"], "paid")
        ids = {ledger_repo.apply_paid_request(tenant_id=1, request_id=req["id"], actor="a")["id"]
               for _ in range(times)}
        assert len(ids) == 1  # same ledger row every time
        n = _db().execute(
            "SELECT COUNT(*) n FROM accounting_ledger_entries "
            "WHERE source_type='payment_collection_request' AND source_id=?", (req["id"],),
        ).fetchone()["n"]
        assert n == 1


@pytest.mark.parametrize("status", ["pending", "rejected", "expired"])
def test_payment_collection_apply_refuses_non_paid(app, status):
    with app.app_context():
        req_repo, ledger_repo = _payment_repos()
        req = req_repo.create(tenant_id=1, payer_type="subscriber", purpose="time_extension",
                              amount=20.0, currency="ILS", provider="manual_wallet",
                              receiver_wallet="0599000000")
        if status != "pending":
            req_repo.update_status(1, req["id"], status)
        with pytest.raises(ValueError):
            ledger_repo.apply_paid_request(tenant_id=1, request_id=req["id"], actor="a")
        n = _db().execute(
            "SELECT COUNT(*) n FROM accounting_ledger_entries "
            "WHERE source_type='payment_collection_request' AND source_id=?", (req["id"],),
        ).fetchone()["n"]
        assert n == 0


def test_payment_collection_amount_must_be_positive(app):
    with app.app_context():
        req_repo, _ = _payment_repos()
        with pytest.raises(ValueError):
            req_repo.create(tenant_id=1, payer_type="subscriber", purpose="card_purchase",
                            amount=0.0, currency="ILS", provider="manual_wallet",
                            receiver_wallet="0599000000")
