"""Store wallet money paths — card-user DEPOSIT (credit) and WITHDRAWAL (debit).

Drives the REAL DepositRequestService / WithdrawalRequestService (which both
fund through the canonical WalletService). Covers: request validation (no money
on create), confirm credits/debits the exact amount, idempotent confirm (a
double-confirm credits/debits ONCE), reject moves no money, withdrawal is
fail-closed (cannot exceed balance), and multi-tenant isolation.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "store_money.db")
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


def _market(tenant_id=1):
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService

    return CardUsersMarketplaceService(tenant_id=tenant_id)


def _deposits(tenant_id=1):
    from app.radius.services.store_deposits import DepositRequestService

    return DepositRequestService(tenant_id=tenant_id)


def _withdrawals(tenant_id=1):
    from app.radius.services.store_withdrawals import WithdrawalRequestService

    return WithdrawalRequestService(tenant_id=tenant_id)


def _wal_minor(card_user_id, tenant_id=1):
    r = _db().execute(
        "SELECT balance_minor FROM wallets WHERE tenant_id=? AND owner_type='card_user' AND owner_id=?",
        (tenant_id, card_user_id)).fetchone()
    return int(r["balance_minor"] or 0) if r else -1


def _user(tenant_id=1, name="Buyer", mobile="0590000000", funded=0.0):
    svc = _market(tenant_id)
    u = svc.create_card_user(display_name=name, mobile=mobile)
    if funded > 0:
        svc.recharge_wallet(card_user_id=u["id"], amount=f"{funded:.2f}", actor="seed")
    return u["id"]


# ════════════════════════════════════════════════════════════════════════
# A — deposits: create=no money, confirm=credit exact, idempotent, reject=none
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("amount,minor", [
    ("1.00", 100), ("5.50", 550), ("33.33", 3333), ("100.00", 10000), ("0.01", 1),
])
def test_deposit_confirm_credits_exact(app, amount, minor):
    with app.app_context():
        uid = _user()
        req = _deposits().create_request(card_user_id=uid, amount_claimed=amount, method="cash")
        assert _wal_minor(uid) == 0           # create moves no money
        _deposits().confirm(req["id"], actor="admin")
        assert _wal_minor(uid) == minor       # confirm credits exactly the claim


def test_deposit_confirm_is_idempotent(app):
    with app.app_context():
        uid = _user()
        req = _deposits().create_request(card_user_id=uid, amount_claimed="20.00", method="cash")
        for _ in range(4):
            _deposits().confirm(req["id"], actor="admin")
        assert _wal_minor(uid) == 2000        # credited once, not 4×


def test_deposit_confirm_adjusted_amount_credits_actual(app):
    with app.app_context():
        uid = _user()
        req = _deposits().create_request(card_user_id=uid, amount_claimed="20.00", method="cash")
        _deposits().confirm(req["id"], actor="admin", confirmed_amount="12.50")
        assert _wal_minor(uid) == 1250        # the corrected (actual) amount, not the claim


def test_deposit_reject_moves_no_money(app):
    with app.app_context():
        uid = _user()
        req = _deposits().create_request(card_user_id=uid, amount_claimed="20.00", method="cash")
        _deposits().reject(req["id"], actor="admin")
        assert _wal_minor(uid) == 0


@pytest.mark.parametrize("bad", ["0.00", "-5.00"])
def test_deposit_create_rejects_non_positive(app, bad):
    from app.radius.services.store_deposits import StoreDepositError

    with app.app_context():
        uid = _user()
        with pytest.raises(StoreDepositError):
            _deposits().create_request(card_user_id=uid, amount_claimed=bad, method="cash")


# ════════════════════════════════════════════════════════════════════════
# B — withdrawals: confirm=debit exact, fail-closed, idempotent, reject=none
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("fund,amount,minor_left", [
    (100.0, "40.00", 6000), (50.0, "50.00", 0), (10.0, "0.01", 999), (20.0, "19.99", 1),
])
def test_withdrawal_confirm_debits_exact(app, fund, amount, minor_left):
    with app.app_context():
        uid = _user(funded=fund)
        req = _withdrawals().create_request(card_user_id=uid, amount=amount,
                                            payee_name="A", payee_account="123")
        assert _wal_minor(uid) == int(round(fund * 100))   # create moves no money
        _withdrawals().confirm(req["id"], actor="admin")
        assert _wal_minor(uid) == minor_left


def test_withdrawal_create_rejects_over_balance(app):
    from app.radius.services.store_withdrawals import StoreWithdrawalError

    with app.app_context():
        uid = _user(funded=10.0)
        with pytest.raises(StoreWithdrawalError):
            _withdrawals().create_request(card_user_id=uid, amount="20.00",
                                          payee_name="A", payee_account="123")
        assert _wal_minor(uid) == 1000  # untouched


def test_withdrawal_confirm_is_idempotent(app):
    with app.app_context():
        uid = _user(funded=100.0)
        req = _withdrawals().create_request(card_user_id=uid, amount="30.00",
                                            payee_name="A", payee_account="123")
        for _ in range(4):
            _withdrawals().confirm(req["id"], actor="admin")
        assert _wal_minor(uid) == 7000  # debited once → 100 − 30


def test_withdrawal_fail_closed_if_balance_dropped_after_request(app):
    from app.radius.services.store_withdrawals import StoreWithdrawalError

    with app.app_context():
        uid = _user(funded=100.0)
        # two withdrawal requests for 60 each pass the create-time guard (100≥60)
        r1 = _withdrawals().create_request(card_user_id=uid, amount="60.00",
                                           payee_name="A", payee_account="1")
        r2 = _withdrawals().create_request(card_user_id=uid, amount="60.00",
                                           payee_name="B", payee_account="2")
        _withdrawals().confirm(r1["id"], actor="admin")   # 100 → 40
        assert _wal_minor(uid) == 4000
        # the second can no longer be covered → structural fail-closed, no negative
        with pytest.raises(StoreWithdrawalError):
            _withdrawals().confirm(r2["id"], actor="admin")
        assert _wal_minor(uid) == 4000


@pytest.mark.parametrize("bad", ["0.00", "-5.00"])
def test_withdrawal_create_rejects_non_positive(app, bad):
    from app.radius.services.store_withdrawals import StoreWithdrawalError

    with app.app_context():
        uid = _user(funded=50.0)
        with pytest.raises(StoreWithdrawalError):
            _withdrawals().create_request(card_user_id=uid, amount=bad,
                                          payee_name="A", payee_account="1")


# ════════════════════════════════════════════════════════════════════════
# C — multi-tenant isolation for store wallet ops
# ════════════════════════════════════════════════════════════════════════
def test_store_money_tenant_isolation(app):
    with app.app_context():
        from app.radius.core.tenant import Tenant
        from app.radius.db.repos import tenants_repo

        t2 = int(tenants_repo.create_tenant(Tenant(id=None, slug="t2-store", name="T2",
                                                   display_name="T2", currency="JOD")).id)
        u1 = _user(tenant_id=1, name="T1", mobile="0590000001")
        u2 = _user(tenant_id=t2, name="T2", mobile="0590000002", funded=0.0)
        req = _deposits(1).create_request(card_user_id=u1, amount_claimed="50.00", method="cash")
        _deposits(1).confirm(req["id"], actor="admin")
        assert _wal_minor(u1, tenant_id=1) == 5000
        assert _wal_minor(u2, tenant_id=t2) == 0  # other tenant untouched
