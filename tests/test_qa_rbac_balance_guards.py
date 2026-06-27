"""QA audit (2026-06): RBAC route-guard parity + balance/credit guards.

PART A — Broken Access Control: pages/actions that the sidebar hid by RBAC but
whose routes were reachable by direct URL for any logged-in admin (even a
1-permission `viewer`). The fix registered the missing endpoints in the central
guard maps (`_PERM_GUARDED` for writes/actions, `_NAV_PERM` for view pages) so
the server — not just the sidebar — enforces the permission. super_admin (and
the primary/owner admin) still bypasses everything.

PART B — Money guards: the sensitive *financial* write endpoints (Business OS
wallet credit/debit, distributor settle, card-user recharge, payment-collection
approve) are now permission-gated, and the wallet ledger refuses to go negative.
The subscriber loan/«سلف» feature is confirmed to be a *bounded, ledgered* credit
instrument (per-loan duration caps + loans-table ledger) — not an unbounded leak.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_qarbac_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _mk(*, is_super=False, role=None):
    from app.radius.db.repos import admins_repo
    rid = None
    if role:
        r = admins_repo.get_role_by_name(role)
        rid = r.id if r else None
    return admins_repo.create_admin(
        username=f"qa_{uuid4().hex[:8]}", password="qa-pass",
        full_name="QA", role_id=rid, is_super_admin=is_super,
    )


def _login(client, username):
    r = client.post("/admin/radius/login",
                    data={"username": username, "password": "qa-pass"},
                    follow_redirects=False)
    assert r.status_code in {302, 303}, r.status_code
    return r


def _csrf(client):
    client.get("/admin/radius/")
    with client.session_transaction() as s:
        return s.get("_csrf_token")


# Newly-gated GET view pages → permission they now require.
GATED_GET = [
    "/admin/radius/admins",
    "/admin/radius/admins/profile-summary",
    "/admin/radius/distributors",
    "/admin/radius/connected-stats",
    "/admin/radius/diagnostics",
    "/admin/radius/lifecycle",
    "/admin/radius/cards",
    "/admin/radius/device-health",
]

# Newly-gated sensitive write endpoints (POST). A non-super viewer must get 403.
GATED_WRITE = [
    ("/admin/radius/finance/wallets", {}),
    ("/admin/radius/finance/wallets/1/credit", {"amount": "5"}),
    ("/admin/radius/finance/wallets/1/debit", {"amount": "5"}),
    ("/admin/radius/distributors/1/settle", {"amount": "5"}),
    ("/admin/radius/card-users/1/recharge", {"amount": "5"}),
    ("/admin/radius/online/coa/set-speed", {"username": "x"}),
    ("/admin/radius/online/coa/set-ip", {"username": "x"}),
    ("/admin/radius/lifecycle/run", {}),
    ("/admin/radius/lifecycle/policies", {"name": "x"}),
    ("/admin/radius/pools", {"name": "x", "cidr": "10.9.9.0/24"}),
    ("/admin/radius/bandwidth", {"name": "x"}),
    ("/admin/radius/mt", {"name": "x"}),
    ("/admin/radius/invoices", {}),
    ("/admin/radius/company-inventory/expenses", {}),
    ("/admin/radius/communications/send", {}),
    ("/admin/radius/events/risk", {}),
    ("/admin/radius/payments/settings", {}),
    ("/admin/radius/print-templates", {}),
]


# ─────────────────────── PART A: GET view-page parity ───────────────────────

def test_viewer_blocked_from_gated_get_pages(app, client):
    _mk(is_super=True)                    # primary/owner occupies id #1
    limited = _mk(is_super=False, role="viewer")
    _login(client, limited.username)
    for url in GATED_GET:
        res = client.get(url, follow_redirects=False)
        assert res.status_code == 403, f"{url} expected 403, got {res.status_code}"


def test_super_admin_reaches_gated_get_pages(app, client):
    owner = _mk(is_super=True)
    _login(client, owner.username)
    for url in GATED_GET:
        res = client.get(url, follow_redirects=False)
        assert res.status_code == 200, f"{url} expected 200, got {res.status_code}"


# ─────────────────────── PART A/B: write-action parity ──────────────────────

def test_viewer_blocked_from_gated_write_endpoints(app, client):
    _mk(is_super=True)
    limited = _mk(is_super=False, role="viewer")
    _login(client, limited.username)
    for url, data in GATED_WRITE:
        tok = _csrf(client)
        payload = dict(data); payload["_csrf_token"] = tok
        res = client.post(url, data=payload, follow_redirects=False)
        assert res.status_code == 403, f"{url} expected 403, got {res.status_code}"


def test_super_admin_not_blocked_from_gated_writes(app, client):
    owner = _mk(is_super=True)
    _login(client, owner.username)
    # A representative money + network write — super must NOT be 403'd by RBAC
    # (the handler may 302/redirect or 4xx on bad data, just never 403).
    for url, data in [("/admin/radius/pools", {"name": "z", "cidr": "10.8.8.0/24"}),
                      ("/admin/radius/finance/wallets", {})]:
        tok = _csrf(client)
        payload = dict(data); payload["_csrf_token"] = tok
        res = client.post(url, data=payload, follow_redirects=False)
        assert res.status_code != 403, f"super blocked on {url} ({res.status_code})"


def test_operator_role_parity(app, client):
    """The operator role (has nas.view/online.view, lacks reports.finance/
    admins.view) keeps the access its permissions grant and loses only what
    they don't — proving toggling a permission actually restricts."""
    _mk(is_super=True)
    op = _mk(is_super=False, role="operator")
    _login(client, op.username)
    # granted by operator perms (nas.view / online.view)
    assert client.get("/admin/radius/diagnostics").status_code == 200
    assert client.get("/admin/radius/connected-stats").status_code == 200
    # NOT granted (reports.finance / admins.view)
    assert client.get("/admin/radius/distributors").status_code == 403
    assert client.get("/admin/radius/admins").status_code == 403


# ───────────────────────────── PART B: money ────────────────────────────────

def test_wallet_ledger_cannot_go_negative(app):
    """Business OS wallet debit beyond balance is refused (no uncovered debit)."""
    from app.radius.services.business_os_finance import (
        WalletService, BusinessOSValidationError)
    svc = WalletService()
    w = svc.create_wallet(tenant_id=1, owner_type="card_user", owner_id=999)
    svc.credit(tenant_id=1, wallet_id=int(w["id"]), amount=10,
               actor_type="system", actor_id=0, reference_type="qa")
    # debit within balance ok
    svc.debit(tenant_id=1, wallet_id=int(w["id"]), amount=4,
              actor_type="system", actor_id=0, reference_type="qa")
    # debit beyond remaining balance → refused
    with pytest.raises(BusinessOSValidationError):
        svc.debit(tenant_id=1, wallet_id=int(w["id"]), amount=100,
                  actor_type="system", actor_id=0, reference_type="qa")


def test_loan_is_bounded_credit_with_ledger(app):
    """«سلف» is an intentional, bounded, ledgered credit instrument:
    per-loan duration caps + a recorded loans-table entry. An over-cap FREE
    loan is rejected; a normal loan is recorded (ledger)."""
    from app.radius.services.accounting import (
        AccountingService, _max_loan_minutes, _max_debt_loan_minutes)
    # caps are real and bounded (not unlimited)
    assert _max_loan_minutes() == 72 * 60
    assert _max_debt_loan_minutes() == 366 * 24 * 60

    # seed demo data to get a real subscriber to lend to
    from app.radius import seed
    seed.seed_demo_data(force=True)
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT username FROM subscribers WHERE tenant_id=1 LIMIT 1").fetchone()
    assert row, "seed produced no subscriber"
    sub_username = row["username"]

    svc = AccountingService(tenant_id=1)
    # a FREE loan over the free cap (72h) must be rejected (bounded)
    with pytest.raises(Exception):
        svc.create_loan({"username": sub_username, "hours": 999}, actor="qa")

    # a within-cap free loan is recorded in the ledger (loans list)
    loan = svc.create_loan({"username": sub_username, "hours": 5}, actor="qa")
    assert loan and loan.get("id")
    listed = svc.list_loans(subscriber_id=int(loan["subscriber_id"]))
    assert any(int(x["id"]) == int(loan["id"]) for x in listed), "loan not ledgered"
