"""Per-manager monetary credit-limit system (trust caps + unified spend gate).

A NEW manager has ZERO trust (balance 0, both caps disabled) → he can do NOTHING
that costs money. The super-admin raises his caps. Every manager money action
funnels through ONE server-side gate (`ManagerCreditService`):

  1. cost ≤ wallet balance → deduct.
  2. else within debt cap → allow the shortfall AS DEBT.
  3. else → BLOCK ("لا يوجد رصيد كافٍ").

Advances (سلف) additionally enforce the independent loan cap ("تجاوزت سقف السلف").
The super-admin / primary owner is the uncapped provider and bypasses the gate;
when he links a package to a manager who can't cover it he may extend debt even
beyond the manager's own cap (super override), via a confirm.

Covers the owner's 6 acceptance scenarios + regression guards.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.db.migrations_runner import run_pending_migrations
from app.radius.db.repos import admins_repo, tenants_repo
from app.radius.services.business_os_finance import WalletService
from app.radius.services.card_pricing import CardPricingError, CardPricingService
from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
from app.radius.services.manager_credit import (
    LOAN_EXCEEDED_MSG,
    NO_BALANCE_MSG,
    SUPER_DEBT_CONFIRM_MSG,
    ManagerCreditConfirmRequired,
    ManagerCreditError,
    ManagerCreditService,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "manager_credit.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


# ── helpers ───────────────────────────────────────────────────────────────
def _owner():
    """Occupy admin id #1 as the primary/owner super-admin (uncapped provider)."""
    return admins_repo.create_admin(
        username=f"owner_{uuid4().hex[:8]}", password="x", is_super_admin=True
    )


def _manager(*, debt_cap=None, loan_cap=None, is_super=False):
    a = admins_repo.create_admin(
        username=f"mgr_{uuid4().hex[:8]}", password="x", is_super_admin=is_super
    )
    changes: dict = {}
    if debt_cap is not None:
        changes["debt_cap_enabled"] = True
        changes["debt_cap_minor"] = int(round(float(debt_cap) * 100))
    if loan_cap is not None:
        changes["loan_cap_enabled"] = True
        changes["loan_cap_minor"] = int(round(float(loan_cap) * 100))
    if changes:
        admins_repo.update_admin(a.id, **changes)
    return admins_repo.get_admin(a.id)


def _fund(manager_id: int, money: float):
    w = WalletService().create_wallet(tenant_id=1, owner_type="manager", owner_id=int(manager_id))
    if float(money) > 0:
        WalletService().credit(
            tenant_id=1, wallet_id=int(w["id"]), amount=f"{float(money):.2f}",
            actor_type="admin", actor_id=1, reference_type="test_fund",
        )


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Plan", 10 * 60, 1, 2.0, "JOD"),
    )
    return int(cur.lastrowid)


def _package(retail="2.00", wholesale="1.50", allowed=None):
    pkg = CardUsersMarketplaceService(tenant_id=1).create_package(
        name="card 2 / wholesale 1.5", plan_id=_plan_id(),
        duration_minutes=10 * 60, speed_down_kbps=3072, speed_up_kbps=768, price=retail,
    )
    return CardPricingService(tenant_id=1).set_package_pricing(
        package_id=pkg["id"], retail_price=retail, wholesale_price=wholesale,
        allowed_manager_ids=allowed or [],
    )


# ════════════════════ SCENARIO 1: new manager = zero trust ════════════════════
def test_s1_new_manager_blocked_on_every_money_action(app):
    with app.app_context():
        _owner()                       # id #1 = uncapped owner
        m = _manager()                 # zero trust: balance 0, caps off
        _fund(m.id, 0)
        svc = ManagerCreditService(tenant_id=1)

        # create a package (cost 150) — blocked
        with pytest.raises(ManagerCreditError) as e1:
            svc.charge(m.id, 15000, kind="card_package", own=True)
        assert "رصيد كافٍ" in str(e1.value)

        # add subscriber balance (50) — blocked
        with pytest.raises(ManagerCreditError) as e2:
            svc.charge(m.id, 5000, kind="subscriber_balance", own=True)
        assert "رصيد كافٍ" in str(e2.value)

        # give a سلف with value (50) — blocked at the funding gate
        adv = svc.evaluate_advance(m.id, 5000)
        assert adv.ok is False and "رصيد كافٍ" in adv.message

        # renew / تجديد (50) — blocked
        with pytest.raises(ManagerCreditError) as e4:
            svc.charge(m.id, 5000, kind="renew", own=True)
        assert "رصيد كافٍ" in str(e4.value)


# ════════════════════ SCENARIO 2: debt cap = 200 ══════════════════════════════
def test_s2_debt_cap_allows_then_blocks(app):
    with app.app_context():
        _owner()
        m = _manager(debt_cap=200.00)   # debt cap 200, balance 0
        _fund(m.id, 0)
        svc = ManagerCreditService(tenant_id=1)

        # 150 package as DEBT (within 200)
        res = svc.charge(m.id, 15000, kind="card_package", own=True)
        assert res["mode"] == "debt"
        assert res["debt_recorded_minor"] == 15000
        assert svc.current_debt_minor(m.id) == 15000

        # the next 60 would push debt to 210 > 200 → blocked
        nxt = svc.evaluate(m.id, 6000, kind="card_package")
        assert nxt.ok is False and "رصيد كافٍ" in nxt.message

        # but exactly hitting 200 is allowed
        ok = svc.charge(m.id, 5000, kind="card_package", own=True)
        assert ok["mode"] == "debt"
        assert svc.current_debt_minor(m.id) == 20000


# ════════════════════ SCENARIO 3: loan cap = 100, independent ══════════════════
def test_s3_loan_cap_independent_of_debt_cap(app):
    with app.app_context():
        _owner()
        # big wallet so advances are funded from wallet (isolate the loan cap),
        # debt cap OFF to prove independence.
        m = _manager(loan_cap=100.00)
        _fund(m.id, 1000.00)
        svc = ManagerCreditService(tenant_id=1)

        svc.charge(m.id, 6000, kind="advance")            # 60 → ok
        svc.charge(m.id, 3000, kind="advance")            # +30 = 90 → ok
        assert svc.current_advances_minor(m.id) == 9000

        # +20 = 110 > 100 → blocked with the loan-cap message
        over = svc.evaluate_advance(m.id, 2000)
        assert over.ok is False and over.message == LOAN_EXCEEDED_MSG
        with pytest.raises(ManagerCreditError) as e:
            svc.charge(m.id, 2000, kind="advance")
        assert str(e.value) == LOAN_EXCEEDED_MSG

        # independence: advances funded from wallet → debt ledger untouched.
        assert svc.current_debt_minor(m.id) == 0
        # debt cap and loan cap don't bleed: a 200 package with debt cap OFF is
        # blocked by the debt gate, not silently allowed by the loan cap.
        assert svc.evaluate(m.id, 200000, kind="card_package").ok is False


# ════════════════════ SCENARIO 4: exact package billing ═══════════════════════
def test_s4_package_billing_exact_amount(app):
    with app.app_context():
        _owner()
        pkg = _package(retail="2.00", wholesale="1.50")
        m = _manager()
        _fund(m.id, 200.00)             # plenty
        result = CardPricingService(tenant_id=1).create_costed_batch(
            package_id=pkg["id"], count=100, responsible_manager_id=m.id,
            creator_type="admin", creator_id=1, actor="admin",
        )
        # card 2 / wholesale 1.5 / count 100 → deduct 150 at once.
        assert result["cost"]["total_wholesale_minor"] == 15000
        wallet = [
            w for w in WalletService().list_wallets(tenant_id=1, owner_type="manager")
            if int(w["owner_id"]) == int(m.id)
        ][0]
        assert wallet["balance"] == "50.00"   # 200 − 150


def test_s4_zero_balance_no_cap_blocks_package(app):
    with app.app_context():
        _owner()
        pkg = _package(retail="2.00", wholesale="1.50")
        m = _manager()                 # 0 balance, no cap
        _fund(m.id, 0)
        with pytest.raises(CardPricingError) as e:
            CardPricingService(tenant_id=1).create_costed_batch(
                package_id=pkg["id"], count=100, responsible_manager_id=m.id,
                creator_type="admin", creator_id=1, actor="admin",
            )
        # legacy "insufficient" wording preserved + the manager-facing toast.
        assert "insufficient" in str(e.value)
        assert "رصيد كافٍ" in str(e.value)


# ════════════════════ SCENARIO 5: super links package → confirm/debt ══════════
def test_s5_super_link_requires_confirm_then_books_debt(app):
    with app.app_context():
        _owner()
        pkg = _package(retail="2.00", wholesale="1.50")
        m = _manager()                 # no balance, no cap
        _fund(m.id, 0)
        svc = ManagerCreditService(tenant_id=1)
        pricing = CardPricingService(tenant_id=1)

        # (a) super links without confirm → ConfirmRequired, nothing created.
        with pytest.raises(ManagerCreditConfirmRequired) as ci:
            pricing.create_costed_batch(
                package_id=pkg["id"], count=100, responsible_manager_id=m.id,
                creator_type="admin", creator_id=1, actor="owner",
                actor_is_super=True, allow_super_debt=False,
            )
        # no cap set → "no balance" warning naming the resulting negative balance.
        assert "رصيد كافٍ" in ci.value.message
        assert "سيصبح" in ci.value.message
        assert ci.value.exceeds_cap is False
        assert ci.value.shortfall_minor == 15000
        assert svc.current_debt_minor(m.id) == 0
        n_batches = db().execute(
            "SELECT COUNT(*) c FROM card_batches WHERE manager_id=?", (m.id,)
        ).fetchone()["c"]
        assert n_batches == 0          # cancel = nothing created

        # (b) super confirms → batch created + 150 booked as the manager's DEBT,
        #     flagged as a super override in the ledger.
        result = pricing.create_costed_batch(
            package_id=pkg["id"], count=100, responsible_manager_id=m.id,
            creator_type="admin", creator_id=1, actor="owner",
            actor_is_super=True, allow_super_debt=True,
        )
        assert result["cost"]["total_wholesale_minor"] == 15000
        assert svc.current_debt_minor(m.id) == 15000     # over-cap debt extended
        override = db().execute(
            "SELECT super_override FROM manager_credit_ledger "
            "WHERE manager_id=? AND kind='debt' ORDER BY id DESC LIMIT 1", (m.id,)
        ).fetchone()
        assert int(override["super_override"]) == 1      # ★ super-override FLAG


# ═══ cap = HARD limit for the manager, SOFT warning for the super (override) ═══
def test_super_may_exceed_manager_debt_cap_with_warning(app):
    """A 200-cap manager: HE is hard-blocked past 200, but the SUPER may knowingly
    push him to -300 — the cap is downgraded to a non-blocking warning that names
    the cap and the resulting negative balance, and the spend proceeds."""
    with app.app_context():
        _owner()
        pkg = _package(retail="2.00", wholesale="1.50")     # 300 = count 200 × 1.5
        m = _manager(debt_cap=200.00)                        # cap 200, balance 0
        _fund(m.id, 0)
        svc = ManagerCreditService(tenant_id=1)
        pricing = CardPricingService(tenant_id=1)

        # (a) the MANAGER himself is HARD-blocked past his own cap (300 > 200).
        with pytest.raises(ManagerCreditError) as me:
            svc.charge(m.id, 30000, kind="card_package", own=True)
        assert "رصيد كافٍ" in str(me.value)
        assert svc.current_debt_minor(m.id) == 0             # nothing booked

        # (b) the SUPER linking a 300 package gets a NON-blocking WARNING that the
        #     cap (200) is exceeded and the balance becomes -300 — not a block.
        with pytest.raises(ManagerCreditConfirmRequired) as ci:
            pricing.create_costed_batch(
                package_id=pkg["id"], count=200, responsible_manager_id=m.id,
                creator_type="admin", creator_id=1, actor="owner",
                actor_is_super=True, allow_super_debt=False,
            )
        assert ci.value.exceeds_cap is True
        assert ci.value.cap_minor == 20000
        assert ci.value.new_effective_minor == -30000
        assert "سقف دين المدير" in ci.value.message
        assert "200.00" in ci.value.message and "-300.00" in ci.value.message
        assert svc.current_debt_minor(m.id) == 0             # still nothing yet

        # (c) on confirm the super override PROCEEDS — manager goes to -300, well
        #     beyond his 200 cap, flagged as a super override.
        pricing.create_costed_batch(
            package_id=pkg["id"], count=200, responsible_manager_id=m.id,
            creator_type="admin", creator_id=1, actor="owner",
            actor_is_super=True, allow_super_debt=True,
        )
        assert svc.current_debt_minor(m.id) == 30000         # -300 effective
        assert svc.current_debt_minor(m.id) > 20000          # exceeded the 200 cap


# ═══════════ SCENARIO 6: ONLY the primary owner is uncapped (owner decision) ═══
def test_s6_only_primary_owner_is_uncapped(app):
    """Owner decision: the uncapped/provider power belongs to the **primary owner
    account alone**. Holding the ``is_super_admin`` flag (the assignable
    ``super_admin`` role, or a license override) no longer grants it — such an
    admin is CAPPED like any manager.

    (Previously this asserted a flag-holding non-owner was uncapped; that bypass
    was the intentional target of this change and is now inverted.)
    """
    with app.app_context():
        _owner()                       # id #1 = primary owner = uncapped provider
        s = _manager(is_super=True)    # flag set but NOT the primary owner
        _fund(s.id, 0)
        svc = ManagerCreditService(tenant_id=1)

        # the flag-holding non-owner is NOT uncapped.
        assert svc.is_uncapped(s.id) is False

        # zero wallet + zero trust (no caps) → spend BLOCKED, not booked as debt.
        with pytest.raises(ManagerCreditError):
            svc.charge(s.id, 9_999_00, kind="card_package")
        # advances are blocked too (loan cap disabled = zero trust).
        with pytest.raises(ManagerCreditError):
            svc.charge(s.id, 5_000_00, kind="advance")

        # the primary owner (id #1) remains the sole uncapped provider.
        owner_id = admins_repo.primary_admin_id()
        assert svc.is_uncapped(owner_id) is True
        res = svc.charge(owner_id, 9_999_00, kind="card_package")
        assert res["mode"] == "debt"   # unlimited provider debt for the owner


# ════════════════════ regression: existing subscriber loan caps intact ════════
def test_subscriber_duration_loan_caps_unchanged(app):
    """The new MANAGER caps must not disturb the SUBSCRIBER-level duration loan
    caps (72h free / 366d debt) — a different layer."""
    from app.radius.services.accounting import _max_debt_loan_minutes, _max_loan_minutes
    assert _max_loan_minutes() == 72 * 60
    assert _max_debt_loan_minutes() == 366 * 24 * 60


# ════════════════════ UI gate: caps are super-only (server-side 403) ══════════
def test_credit_caps_post_by_non_super_is_403(app):
    with app.app_context():
        owner = _owner()
        target = _manager()
    client = app.test_client()
    # non-super session attempts to POST the cap fields → 403.
    with client.session_transaction() as sess:
        sess["admin_id"] = target.id
        sess["admin_user"] = "nonsuper"
        sess["admin_name"] = "Non Super"
        sess["is_super_admin"] = False
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "t"
        sess["permissions"] = ["admins.view", "admins.edit"]
    res = client.post(
        f"/admin/radius/admins/{target.id}",
        data={
            "_csrf_token": "t", "full_name": "x", "credit_caps_present": "1",
            "debt_cap_enabled": "1", "debt_cap_amount": "999",
        },
        follow_redirects=False,
    )
    assert res.status_code == 403


def test_credit_caps_section_renders_for_super(app):
    with app.app_context():
        owner = _owner()
        target = _manager(debt_cap=200.00, loan_cap=100.00)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = owner.id
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "t"
        sess["admin_user"] = "owner"
    res = client.get(f"/admin/radius/admins/{target.id}/edit")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "حدود الائتمان المالي" in html          # super-only credit section rendered
    assert 'name="credit_caps_present"' in html
    assert 'value="200.00"' in html and 'value="100.00"' in html


def test_credit_caps_section_hidden_for_non_super(app):
    with app.app_context():
        _owner()
        target = _manager()
        viewer = _manager()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = viewer.id
        sess["is_super_admin"] = False
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "t"
        sess["permissions"] = ["admins.view", "admins.edit"]
    res = client.get(f"/admin/radius/admins/{target.id}/edit")
    # Non-super either can't see the section or is blocked from the page entirely;
    # in no case may the credit-cap fields appear.
    if res.status_code == 200:
        assert "credit_caps_present" not in res.get_data(as_text=True)


def test_credit_caps_saved_by_super(app):
    with app.app_context():
        owner = _owner()
        target = _manager()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = owner.id
        sess["admin_user"] = "owner"
        sess["admin_name"] = "Owner"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "t"
    res = client.post(
        f"/admin/radius/admins/{target.id}",
        data={
            "_csrf_token": "t", "full_name": "x", "credit_caps_present": "1",
            "debt_cap_enabled": "1", "debt_cap_amount": "200",
            "loan_cap_amount": "0",
        },
        follow_redirects=False,
    )
    assert res.status_code in (302, 303)
    with app.app_context():
        saved = admins_repo.get_admin(target.id)
        assert saved.debt_cap_enabled is True
        assert saved.debt_cap_minor == 20000
        assert saved.loan_cap_enabled is False
