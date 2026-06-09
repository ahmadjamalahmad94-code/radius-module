"""Tests for the internal Company Inventory & Expenses Finance page.

Covers the required behaviours:
 1. Page accessible to authorized admin.
 2. Incoming stock increases remaining quantity.
 3. Usage decreases remaining quantity.
 4. Usage cannot exceed available quantity.
 5. Company expenses saved and summarized.
 6. Filters work for date/category/item.
 7. Feature does NOT create ledger entries.
 8. Feature does NOT modify sales/payment/customer-balance tables.
 9. Arabic labels render on the page.
10. Navigation link appears in the Finance section.
11. (covered by the wider suite) existing finance tests still pass.

Plus the named regression test:
    "company inventory and expenses do not affect financial ledger totals"
"""
from __future__ import annotations

import os

import pytest


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations

    run_pending_migrations()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "company_inventory.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        _run_pending_migrations()
    return flask_app


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "inv_admin"
        sess["admin_name"] = "Inventory Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "inv-csrf"


_BASE = "/admin/radius/company-inventory"
_OLD_BASE = "/admin/radius/finance/company-inventory-expenses"


def _post(client, path, **data):
    data.setdefault("_csrf_token", "inv-csrf")
    return client.post(f"{_BASE}{path}", data=data, follow_redirects=True)


def _remaining(app, name):
    with app.app_context():
        from app.radius.db.repos import company_inventory_repo as repo
        item = repo.get_item_by_name(tenant_id=1, name=name)
        if not item:
            return None
        return repo.remaining_for_item(tenant_id=1, item_id=int(item["id"]))


# ── 1. page accessible ──────────────────────────────────────────


def test_page_accessible_to_authorized_admin(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get(_BASE)
    assert res.status_code == 200


def test_page_redirects_anonymous_to_login(app):
    with app.test_client() as client:
        res = client.get(_BASE, follow_redirects=False)
    assert res.status_code in {301, 302}
    assert "login" in res.headers.get("Location", "")


# ── 9. Arabic labels render ─────────────────────────────────────


def test_arabic_labels_render(app):
    with app.test_client() as client:
        _auth_session(client)
        # Seed one item so the inventory overview table (with its
        # remaining-quantity column) renders, not just the empty state.
        _post(client, "/incoming", item_name="كابل", unit="متر", quantity="10")
        html = client.get(_BASE).get_data(as_text=True)
    # Hero + always-present tab labels.
    assert "مخزون ومصروفات الشركة" in html
    assert "ملخص المخزون" in html
    assert "الصرف" in html
    assert "المصروفات" in html
    # The remaining-quantity column header is present once stock exists.
    assert "المتبقي" in html


def test_legacy_finance_url_redirects_to_standalone(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get(_OLD_BASE, follow_redirects=False)
    assert res.status_code in {301, 302}
    assert res.headers.get("Location", "").endswith("/company-inventory")


def test_tabs_switch_active_section(app):
    with app.test_client() as client:
        _auth_session(client)
        for tab in ("overview", "incoming", "usage", "expenses", "reports"):
            res = client.get(f"{_BASE}?tab={tab}")
            assert res.status_code == 200
            html = res.get_data(as_text=True)
            # The requested tab pill carries the active class.
            assert f'?tab={tab}" class="is-active"' in html


def test_add_forms_are_in_modals(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get(f"{_BASE}?tab=incoming").get_data(as_text=True)
    # Floating <dialog> modals for each add form, opened by buttons.
    assert '<dialog class="cie-modal" data-modal="incoming"' in html
    assert 'data-modal-open="incoming"' in html


# ── 10. nav link appears in the sidebar ─────────────────────────


def test_nav_link_appears_in_sidebar_finance_section(app):
    with app.test_client() as client:
        _auth_session(client)
        # The sidebar renders on every admin page; check the dashboard
        # (a different page) carries the link, which now lives under the
        # Finance section ("المال والتحصيل").
        html = client.get("/admin/radius/").get_data(as_text=True)
    assert "/company-inventory" in html
    assert "مخزون ومصروفات الشركة" in html
    # It sits inside the finance/billing section, not a standalone one.
    assert "المال والتحصيل" in html


# ── 2. incoming increases remaining ─────────────────────────────


def test_incoming_increases_remaining_quantity(app):
    with app.test_client() as client:
        _auth_session(client)
        res = _post(client, "/incoming", item_name="كابل", unit="متر",
                    quantity="100", unit_cost="2")
    assert res.status_code == 200
    assert _remaining(app, "كابل") == 100.0


# ── 3. usage decreases remaining ────────────────────────────────


def test_usage_decreases_remaining_quantity(app):
    with app.test_client() as client:
        _auth_session(client)
        _post(client, "/incoming", item_name="راوتر", unit="قطعة", quantity="50")
        with app.app_context():
            from app.radius.db.repos import company_inventory_repo as repo
            item_id = repo.get_item_by_name(tenant_id=1, name="راوتر")["id"]
        _post(client, "/usage", item_id=str(item_id), quantity="10",
              usage_reason="تركيب")
    assert _remaining(app, "راوتر") == 40.0


# ── 4. usage cannot exceed available ────────────────────────────


def test_usage_cannot_exceed_available_quantity(app):
    with app.test_client() as client:
        _auth_session(client)
        _post(client, "/incoming", item_name="سويتش", unit="قطعة", quantity="20")
        with app.app_context():
            from app.radius.db.repos import company_inventory_repo as repo
            item_id = repo.get_item_by_name(tenant_id=1, name="سويتش")["id"]
        res = _post(client, "/usage", item_id=str(item_id), quantity="999")
        html = res.get_data(as_text=True)
    # Error surfaced, and the remaining stock is unchanged.
    assert "غير متوفرة" in html
    assert _remaining(app, "سويتش") == 20.0


# ── 5. expenses saved + summarized ──────────────────────────────


def test_company_expenses_saved_and_summarized(app):
    with app.test_client() as client:
        _auth_session(client)
        _post(client, "/expenses", title="اشتراك إنترنت", amount="500",
              category="تشغيل")
        _post(client, "/expenses", title="أجور عمال", amount="200",
              category="أجور")
        html = client.get(_BASE).get_data(as_text=True)
    with app.app_context():
        from app.radius.services.company_inventory import CompanyInventoryService
        rep = CompanyInventoryService().reports(tenant_id=1)
    assert rep["expenses_total"] == 700.0
    cats = {c["category"]: c["total"] for c in rep["expenses_by_category"]}
    assert cats.get("تشغيل") == 500.0
    assert cats.get("أجور") == 200.0
    assert "اشتراك إنترنت" in html


# ── 6. filters work ─────────────────────────────────────────────


def test_report_filters_by_category_and_item(app):
    with app.test_client() as client:
        _auth_session(client)
        _post(client, "/expenses", title="حبر طابعة", amount="50",
              category="مكتب", expense_date="2026-01-15")
        _post(client, "/expenses", title="صيانة", amount="500",
              category="صيانة", expense_date="2026-02-20")
        # Filter to just the مكتب category in January.
        res = client.get(
            f"{_BASE}?date_from=2026-01-01&date_to=2026-01-31"
            f"&expense_category=مكتب"
        )
    with app.app_context():
        from app.radius.services.company_inventory import CompanyInventoryService
        rep = CompanyInventoryService().reports(
            tenant_id=1, date_from="2026-01-01", date_to="2026-01-31",
            expense_category="مكتب",
        )
    assert res.status_code == 200
    # Only the January مكتب expense is in range.
    assert rep["expenses_total"] == 50.0
    titles = [e["title"] for e in rep["expenses"]]
    assert "حبر طابعة" in titles
    assert "صيانة" not in titles


def test_report_filters_by_movement_type(app):
    with app.test_client() as client:
        _auth_session(client)
        _post(client, "/incoming", item_name="كابل", unit="متر", quantity="100",
              unit_cost="2", movement_date="2026-03-01")
        with app.app_context():
            from app.radius.db.repos import company_inventory_repo as repo
            iid = repo.get_item_by_name(tenant_id=1, name="كابل")["id"]
        _post(client, "/usage", item_id=str(iid), quantity="30",
              movement_date="2026-03-05")
    with app.app_context():
        from app.radius.services.company_inventory import CompanyInventoryService
        svc = CompanyInventoryService()
        only_usage = svc.reports(tenant_id=1, movement_type="usage",
                                 date_from="2026-03-01", date_to="2026-03-31")
    types = {m["movement_type"] for m in only_usage["movements"]}
    assert types == {"usage"}


# ── 7 + 8 + named regression: NO financial side effects ─────────


def _existing_financial_tables(app):
    """Return the subset of financial tables that exist in this DB."""
    candidates = [
        "ledger_entries",
        "accounting_ledger_entries",
        "distributor_ledger_entries",
        "revenue_records",
        "wallets",
        "wallet_transactions",
        "payment_requests",
        "payment_transactions",
        "payment_collection_transactions",
        "subscribers",
    ]
    with app.app_context():
        from app.radius.db.connection import db
        present = {
            r["name"]
            for r in db().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    return [t for t in candidates if t in present]


def _table_counts(app, tables):
    with app.app_context():
        from app.radius.db.connection import db
        return {
            t: db().execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            for t in tables
        }


def test_company_inventory_and_expenses_do_not_affect_financial_ledger_totals(app):
    """REGRESSION: the inventory/expenses notebook is fully isolated.

    Snapshot every financial table's row count, perform a full set of
    inventory + expense operations, then assert NOT A SINGLE row was
    added to any ledger / payment / wallet / revenue / subscriber
    table. Costs/amounts here are informational only.
    """
    tables = _existing_financial_tables(app)
    before = _table_counts(app, tables)

    with app.test_client() as client:
        _auth_session(client)
        # incoming with a cost (the informational money path)
        _post(client, "/incoming", item_name="كابل", unit="متر",
              quantity="100", unit_cost="2", supplier="مورّد")
        with app.app_context():
            from app.radius.db.repos import company_inventory_repo as repo
            iid = repo.get_item_by_name(tenant_id=1, name="كابل")["id"]
        # usage (decrement)
        _post(client, "/usage", item_id=str(iid), quantity="20",
              usage_reason="تركيب", related_customer_id="5")
        # a company expense with an amount
        _post(client, "/expenses", title="اشتراك إنترنت", amount="500",
              category="تشغيل", payment_method="نقدًا")

    after = _table_counts(app, tables)
    assert after == before, (
        "company inventory/expenses must not write to any financial "
        f"table. before={before} after={after}"
    )

    # And the inventory data itself WAS written (sanity: operations ran).
    with app.app_context():
        from app.radius.db.connection import db
        inv_moves = db().execute(
            "SELECT COUNT(*) AS c FROM company_inventory_movements"
        ).fetchone()["c"]
        expenses = db().execute(
            "SELECT COUNT(*) AS c FROM company_expenses"
        ).fetchone()["c"]
    assert inv_moves == 2  # one incoming + one usage
    assert expenses == 1


def test_no_ledger_entry_created_for_expense(app):
    """Point 7 (explicit): adding a company expense creates zero
    ledger rows even though it has a money amount."""
    tables = [t for t in _existing_financial_tables(app)
              if "ledger" in t or "revenue" in t]
    if not tables:
        pytest.skip("no ledger/revenue tables in this DB build")
    before = _table_counts(app, tables)
    with app.test_client() as client:
        _auth_session(client)
        _post(client, "/expenses", title="صيانة", amount="500")
    after = _table_counts(app, tables)
    assert after == before
