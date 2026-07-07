"""Balance-movements currency display + owner demo-seed cleanup.

Two behaviours are locked in here:

ISSUE 1 — currency display. The «المبلغ» column on the balance-movements and
cash-transactions reports must render in the SYSTEM currency (settings-driven
``money`` filter), never a hardcoded symbol. A row physically stored with an old
``JOD`` currency must still display in the configured currency (₪ when ILS, $
when USD) — proving the symbol is settings-driven, not baked into the template.

ISSUE 2 — demo-seed cleanup. The owner-run cleanup deletes ONLY rows whose
executor is exactly ``demo-seed`` and leaves every real movement untouched, is
idempotent, and is owner-gated. Also: the demo seeder never injects money
movements unless money is explicitly opted in.

Run this file on its own.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "balmov.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_DEMO_SEED", raising=False)
    monkeypatch.delenv("HOBERADIUS_DEMO_MONEY", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        yield application
    reset_for_tests(None)


@pytest.fixture
def client(app):
    return app.test_client()


# ── helpers ───────────────────────────────────────────────────────────

def _make_owner():
    """Create a super admin and designate it as THE owner (bridge.owner_admins),
    so is_primary_owner() matches it regardless of the bootstrap admin's id."""
    from app.radius.db.repos import admins_repo
    u = f"owner_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw123456",
                             full_name="Owner", is_super_admin=True)
    admins_repo.set_designated_owners([u])
    return u


def _make_manager():
    from app.radius.db.repos import admins_repo
    u = f"mgr_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw123456",
                             full_name="Manager", is_super_admin=False)
    return u


def _login(client, username):
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": "pw123456"},
                      follow_redirects=False)
    assert res.status_code in (302, 303), res.status_code


def _set_currency(code):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "billing.currency", code)


def _ledger(operator, *, currency="JOD", amount=8.0, direction="credit"):
    """Insert an accounting_ledger_entries row (balance-movements source)."""
    from app.radius.db.connection import db
    from app.radius.db.helpers import now_iso
    db().execute(
        """INSERT INTO accounting_ledger_entries(
               tenant_id, entry_type, direction, amount, currency,
               operator, source_type, status, created_at)
           VALUES(1,?,?,?,?,?,?, 'posted', ?)""",
        ("loan", direction, amount, currency, operator, "system", now_iso()),
    )
    db().commit()


def _subscriber():
    from app.radius.core.constants import USER_TYPE_SUBSCRIBER
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    s = subscribers_repo.upsert_subscriber(
        Subscriber(id=None, username=f"sub_{uuid4().hex[:6]}",
                   password="x", user_type=USER_TYPE_SUBSCRIBER))
    return s.id, s.username


def _payment(created_by, *, currency="JOD", amount=7.0):
    """Insert a payment_transactions row (cash-transactions source)."""
    from app.radius.db.connection import db
    from app.radius.db.helpers import now_iso
    sid, uname = _subscriber()
    db().execute(
        """INSERT INTO payment_transactions(
               tenant_id, subscriber_id, username, amount, currency,
               method, status, effective_price, created_by, created_at)
           VALUES(1,?,?,?,?, 'cash', 'posted', ?, ?, ?)""",
        (sid, uname, amount, currency, amount, created_by, now_iso()),
    )
    db().commit()


def _loan(created_by, *, currency="JOD", amount=3.0):
    from app.radius.db.connection import db
    from app.radius.db.helpers import now_iso
    sid, uname = _subscriber()
    now = now_iso()
    db().execute(
        """INSERT INTO loan_entries(
               tenant_id, subscriber_id, username, duration_minutes, amount,
               currency, reason, starts_at, ends_at, created_by, created_at)
           VALUES(1,?,?, 60, ?, ?, 'demo loan', ?, ?, ?, ?)""",
        (sid, uname, amount, currency, now, now, created_by, now),
    )
    db().commit()


def _count(table, where, params):
    from app.radius.db.connection import db
    return int(db().execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE {where}", params).fetchone()["c"])


# ═══════════════════ ISSUE 1 — currency is settings-driven ═══════════════════

def test_balance_movements_amount_uses_configured_currency_not_hardcoded(app, client):
    """A row physically stored as JOD renders in the configured currency (₪/$),
    never the hardcoded Dinar «د.أ»."""
    owner = _make_owner()
    with app.app_context():
        _set_currency("ILS")
        _ledger("real-admin", currency="JOD", amount=8.0)  # stored JOD on purpose
    _login(client, owner)

    res = client.get("/admin/radius/reports/balance_movements")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "₪" in html                 # configured currency shown
    assert "د.أ" not in html           # hardcoded Dinar must NOT leak

    # Flip the configured currency → the SAME row now renders in USD.
    with app.app_context():
        _set_currency("USD")
    html2 = client.get("/admin/radius/reports/balance_movements").get_data(as_text=True)
    assert "$" in html2
    assert "د.أ" not in html2


def test_balance_movements_shows_owner_cleanup_banner_when_demo_rows_present(app, client):
    owner = _make_owner()
    with app.app_context():
        _set_currency("ILS")
        _ledger("demo-seed")            # a demo-seed row is present
    _login(client, owner)
    html = client.get("/admin/radius/reports/balance_movements").get_data(as_text=True)
    # Banner-unique copy (the sidebar link exists regardless, so match the banner).
    assert "يحتوي السجل على حركات تجريبيّة" in html


def test_balance_movements_hides_banner_when_no_demo_rows(app, client):
    owner = _make_owner()
    with app.app_context():
        _set_currency("ILS")
        _ledger("real-admin")           # only a real movement
    _login(client, owner)
    html = client.get("/admin/radius/reports/balance_movements").get_data(as_text=True)
    assert "يحتوي السجل على حركات تجريبيّة" not in html


def test_cash_transactions_amount_uses_configured_currency(app, client):
    owner = _make_owner()
    with app.app_context():
        _set_currency("ILS")
        _payment("real-cashier", currency="JOD", amount=7.0)
    _login(client, owner)

    html = client.get("/admin/radius/reports/cash_transactions").get_data(as_text=True)
    assert "₪" in html
    assert "د.أ" not in html


# ═══════════════════ ISSUE 2 — demo-seed cleanup precision ═══════════════════

def test_demo_cleanup_deletes_only_demo_seed_rows(app):
    from app.radius.services.demo_cleanup import get_demo_cleanup_service
    with app.app_context():
        # Two of each: one demo-seed tagged, one a real movement.
        _ledger("demo-seed")
        _ledger("real-admin")
        _payment("demo-seed")
        _payment("real-cashier")
        _loan("demo-seed")
        _loan("real-operator")

        preview = get_demo_cleanup_service().preview(tenant_id=1)
        assert preview["total"] == 3          # one demo row per table
        assert preview["marker"] == "demo-seed"

        result = get_demo_cleanup_service().purge(tenant_id=1)
        assert result["total_deleted"] == 3

        # Real movements survive; demo movements are gone.
        assert _count("accounting_ledger_entries", "operator = ?", ("demo-seed",)) == 0
        assert _count("accounting_ledger_entries", "operator = ?", ("real-admin",)) == 1
        assert _count("payment_transactions", "created_by = ?", ("demo-seed",)) == 0
        assert _count("payment_transactions", "created_by = ?", ("real-cashier",)) == 1
        assert _count("loan_entries", "created_by = ?", ("demo-seed",)) == 0
        assert _count("loan_entries", "created_by = ?", ("real-operator",)) == 1


def test_demo_cleanup_is_idempotent(app):
    from app.radius.services.demo_cleanup import get_demo_cleanup_service
    with app.app_context():
        _ledger("demo-seed")
        _ledger("real-admin")
        first = get_demo_cleanup_service().purge(tenant_id=1)
        assert first["total_deleted"] == 1
        second = get_demo_cleanup_service().purge(tenant_id=1)
        assert second["total_deleted"] == 0            # nothing left to delete
        assert _count("accounting_ledger_entries", "operator = ?", ("real-admin",)) == 1


def test_demo_cleanup_does_not_match_lookalike_operators(app):
    """Exact match only — a real operator whose name merely CONTAINS the marker
    is never touched."""
    from app.radius.services.demo_cleanup import get_demo_cleanup_service
    with app.app_context():
        _ledger("demo-seed-helper")     # not the exact marker
        _ledger("pre-demo-seed")        # not the exact marker
        result = get_demo_cleanup_service().purge(tenant_id=1)
        assert result["total_deleted"] == 0
        assert _count("accounting_ledger_entries", "1=1", ()) == 2


# ═══════════════════ ISSUE 2 — route guard + confirm word ═══════════════════

def test_demo_cleanup_page_forbidden_for_non_owner(app, client):
    manager = _make_manager()
    _login(client, manager)
    assert client.get("/admin/radius/demo-cleanup").status_code == 403


def test_demo_cleanup_run_happy_path_backs_up_then_deletes_only_demo(app, client):
    """End-to-end owner flow: confirm word → mandatory backup → delete demo rows
    only. Proves the button the owner clicks actually works and is precise."""
    owner = _make_owner()
    with app.app_context():
        _ledger("demo-seed")
        _ledger("real-admin")
    _login(client, owner)
    client.get("/admin/radius/demo-cleanup")
    with client.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    res = client.post("/admin/radius/demo-cleanup/run",
                      json={"confirm": "حذف"},
                      headers={"X-CSRFToken": tok})
    body = res.get_json()
    assert body["ok"] is True, body
    assert body["total_deleted"] == 1
    assert body.get("backup")            # a backup was taken before deleting
    with app.app_context():
        assert _count("accounting_ledger_entries", "operator = ?", ("demo-seed",)) == 0
        assert _count("accounting_ledger_entries", "operator = ?", ("real-admin",)) == 1


def test_demo_cleanup_run_requires_confirm_word(app, client):
    owner = _make_owner()
    with app.app_context():
        _ledger("demo-seed")
    _login(client, owner)
    client.get("/admin/radius/demo-cleanup")
    with client.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    res = client.post("/admin/radius/demo-cleanup/run",
                      json={"confirm": "wrong"},
                      headers={"X-CSRFToken": tok})
    body = res.get_json()
    assert body["ok"] is False and body["code"] == "confirm"
    # The demo row is still there — nothing was deleted without confirmation.
    with app.app_context():
        assert _count("accounting_ledger_entries", "operator = ?", ("demo-seed",)) == 1


# ═══════════════════ ISSUE 2a — seeder never seeds money by default ═══════════

def test_seed_does_not_inject_money_movements_by_default(app):
    from app.radius.seed import seed_demo_data
    with app.app_context():
        seed_demo_data()   # no force, no HOBERADIUS_DEMO_MONEY flag
        assert _count("payment_transactions", "1=1", ()) == 0
        assert _count("loan_entries", "1=1", ()) == 0


def test_seed_injects_money_only_when_explicitly_forced(app):
    from app.radius.seed import seed_demo_data
    with app.app_context():
        summary = seed_demo_data(force=True)
        assert summary["payments"] > 0
        assert summary["loans"] > 0
        # And every seeded money row carries the exact demo marker.
        assert _count("payment_transactions", "created_by = ?", ("demo-seed",)) > 0
        assert _count("loan_entries", "created_by = ?", ("demo-seed",)) > 0
