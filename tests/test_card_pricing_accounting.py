from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.db.migrations_runner import run_pending_migrations
from app.radius.db.repos import admins_repo, tenants_repo
from app.radius.services.business_os_finance import WalletService
from app.radius.services.card_pricing import CardPricingError, CardPricingService
from app.radius.services.card_users_marketplace import CardUsersMarketplaceService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "card_pricing.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
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


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "pricing_admin"
        sess["admin_name"] = "Pricing Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "pricing-csrf"


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Pricing Plan", 10 * 60, 1, 8.0, "JOD"),
    )
    return int(cur.lastrowid)


def _package(app):
    with app.app_context():
        package = CardUsersMarketplaceService(tenant_id=1).create_package(
            name="10 hours / 3 Mbps",
            plan_id=_plan_id(),
            duration_minutes=10 * 60,
            speed_down_kbps=3072,
            speed_up_kbps=768,
            price="8.00",
        )
        package = CardPricingService(tenant_id=1).set_package_pricing(
            package_id=package["id"],
            retail_price="8.00",
            wholesale_price="5.00",
            min_price="6.00",
            max_discount="2.00",
            allowed_manager_ids=[77],
        )
    return package


def _manager_wallet(manager_id: int, amount: str = "100.00"):
    wallet = WalletService().create_wallet(tenant_id=1, owner_type="manager", owner_id=manager_id)
    WalletService().credit(
        tenant_id=1,
        wallet_id=wallet["id"],
        amount=amount,
        actor_type="admin",
        actor_id=1,
        reference_type="test",
    )
    return wallet


def test_price_snapshot_is_immutable_after_package_price_change(app):
    package = _package(app)
    with app.app_context():
        _manager_wallet(77)
        service = CardPricingService(tenant_id=1)
        result = service.create_costed_batch(
            package_id=package["id"],
            count=4,
            responsible_manager_id=77,
            creator_type="admin",
            creator_id=1,
            actor="admin",
        )
        snapshot_before = db().execute(
            "SELECT * FROM price_snapshots WHERE id=?",
            (result["cost"]["price_snapshot_id"],),
        ).fetchone()
        service.set_package_pricing(
            package_id=package["id"],
            retail_price="9.00",
            wholesale_price="6.00",
        )
        snapshot_after = db().execute(
            "SELECT * FROM price_snapshots WHERE id=?",
            (result["cost"]["price_snapshot_id"],),
        ).fetchone()

    assert snapshot_before["retail_price_minor"] == 800
    assert snapshot_before["wholesale_price_minor"] == 500
    assert snapshot_after["retail_price_minor"] == 800
    assert snapshot_after["wholesale_price_minor"] == 500


def test_batch_cost_charged_to_responsible_manager_wallet(app):
    package = _package(app)
    with app.app_context():
        _manager_wallet(77, amount="30.00")
        result = CardPricingService(tenant_id=1).create_costed_batch(
            package_id=package["id"],
            count=3,
            responsible_manager_id=77,
            creator_type="admin",
            creator_id=1,
            actor="admin",
        )
        wallet = [
            item for item in WalletService().list_wallets(tenant_id=1, owner_type="manager")
            if item["owner_id"] == 77
        ][0]
        ledger = db().execute(
            "SELECT * FROM ledger_entries WHERE id=?",
            (result["cost"]["ledger_entry_id"],),
        ).fetchone()
        event = db().execute(
            "SELECT * FROM business_events WHERE event_key='card_batch.costed' AND target_id=?",
            (result["batch"]["id"],),
        ).fetchone()

    assert result["cost"]["total_wholesale_minor"] == 1500
    assert wallet["balance"] == "15.00"
    assert ledger["entry_type"] == "batch_creation"
    assert event is not None


def test_admin_created_batch_assigned_to_manager_and_profit_recorded(app):
    package = _package(app)
    with app.app_context():
        _manager_wallet(77)
        result = CardPricingService(tenant_id=1).create_costed_batch(
            package_id=package["id"],
            count=2,
            responsible_manager_id=77,
            creator_type="admin",
            creator_id=1,
            actor="admin",
        )
        revenue = db().execute(
            "SELECT * FROM revenue_records WHERE id=?",
            (result["cost"]["revenue_record_id"],),
        ).fetchone()

    assert result["batch"]["manager_id"] == 77
    assert result["batch"]["created_by"] == "admin"
    assert revenue["retail_price_minor"] == 1600
    assert revenue["wholesale_cost_minor"] == 1000
    assert revenue["net_profit_minor"] == 600


def test_manager_allowlist_and_insufficient_balance_are_enforced(app):
    package = _package(app)
    with app.app_context():
        _manager_wallet(77, amount="1.00")
        service = CardPricingService(tenant_id=1)
        with pytest.raises(CardPricingError, match="not allowed"):
            service.create_costed_batch(package_id=package["id"], count=1, responsible_manager_id=88)
        with pytest.raises(CardPricingError, match="insufficient"):
            service.create_costed_batch(package_id=package["id"], count=1, responsible_manager_id=77)


def test_card_pricing_admin_page_is_retired(app):
    package = _package(app)
    with app.test_client() as client:
        _auth_session(client)
        page = client.get("/admin/radius/card-pricing")
        summary = client.get("/admin/radius/card-pricing/summary.json")

    assert package["name"]
    assert page.status_code == 404
    assert summary.status_code == 404
