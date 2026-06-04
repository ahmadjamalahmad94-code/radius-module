from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.services.business_os_finance import WalletService
from app.radius.services.card_pricing import CardPricingService
from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
from app.radius.services.manager_distributor_ops import (
    ManagerDistributorError,
    ManagerDistributorOpsService,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "manager_distributor_ops.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "ops_admin"
        sess["admin_name"] = "Ops Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "ops-csrf"


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Operator Plan", 12 * 60, 1, 8.0, "JOD"),
    )
    return int(cur.lastrowid)


def _priced_package() -> dict:
    package = CardUsersMarketplaceService(tenant_id=1).create_package(
        name="Operator Package",
        plan_id=_plan_id(),
        duration_minutes=12 * 60,
        speed_down_kbps=4096,
        speed_up_kbps=1024,
        price="8.00",
    )
    return CardPricingService(tenant_id=1).set_package_pricing(
        package_id=package["id"],
        retail_price="8.00",
        wholesale_price="5.00",
        allowed_manager_ids=[1],
    )


def test_manager_permission_denial_blocks_subscriber_creation(app):
    with app.app_context():
        service = ManagerDistributorOpsService(tenant_id=1)
        service.set_policy(
            entity_type="manager",
            entity_id=1,
            permissions={"can_create_subscriber": False},
        )

        with pytest.raises(ManagerDistributorError, match="permission denied"):
            service.create_subscriber_without_activation(
                manager_id=1,
                username="pending-user",
                password="secret",
            )


def test_manager_permission_allows_pending_subscriber_without_radius_activation(app):
    with app.app_context():
        service = ManagerDistributorOpsService(tenant_id=1)
        service.set_policy(
            entity_type="manager",
            entity_id=1,
            permissions={"can_create_subscriber": True},
        )
        result = service.create_subscriber_without_activation(
            manager_id=1,
            username="pending-user",
            password="secret",
        )
        profile = service.profile(entity_type="manager", entity_id=1)

    assert result["applied_to_radius"] is False
    assert result["subscriber"].status == "pending"
    assert profile["subscribers"][0]["username"] == "pending-user"


def test_manager_wallet_deducted_for_costed_card_batch(app):
    with app.app_context():
        package = _priced_package()
        WalletService().create_wallet(tenant_id=1, owner_type="manager", owner_id=1)
        service = ManagerDistributorOpsService(tenant_id=1)
        service.recharge_wallet(entity_type="manager", entity_id=1, amount="30.00", actor="qa")

        CardPricingService(tenant_id=1).create_costed_batch(
            package_id=package["id"],
            count=3,
            responsible_manager_id=1,
            creator_type="admin",
            creator_id=1,
            actor="qa",
        )
        profile = service.profile(entity_type="manager", entity_id=1)

    assert profile["wallet"]["balance"] == "15.00"
    assert profile["batches"][0]["manager_id"] == 1


def test_credit_limit_enforced_for_limited_actions(app):
    with app.app_context():
        service = ManagerDistributorOpsService(tenant_id=1)
        service.set_policy(
            entity_type="manager",
            entity_id=1,
            permissions={"can_give_loan": True},
            credit_limit="10.00",
        )

        with pytest.raises(ManagerDistributorError, match="credit limit exceeded"):
            service.assert_allowed(
                entity_type="manager",
                entity_id=1,
                permission="can_give_loan",
                amount="11.00",
            )


def test_manager_profit_share_calculated_from_card_batch_margin(app):
    with app.app_context():
        package = _priced_package()
        service = ManagerDistributorOpsService(tenant_id=1)
        service.set_policy(
            entity_type="manager",
            entity_id=1,
            permissions={"can_create_batch": True},
            profit_share_percent=40,
        )
        service.recharge_wallet(entity_type="manager", entity_id=1, amount="100.00", actor="qa")
        CardPricingService(tenant_id=1).create_costed_batch(
            package_id=package["id"],
            count=2,
            responsible_manager_id=1,
            creator_type="admin",
            creator_id=1,
            actor="qa",
        )
        profile = service.profile(entity_type="manager", entity_id=1)

    assert profile["profit"]["original_price"] == "16.00"
    assert profile["profit"]["wholesale_cost"] == "10.00"
    assert profile["profit"]["net_margin"] == "6.00"
    assert profile["profit"]["manager_share"] == "2.40"
    assert profile["profit"]["company_share"] == "3.60"


def test_operator_scope_lists_are_separated(app):
    with app.app_context():
        service = ManagerDistributorOpsService(tenant_id=1)
        managers = service.list_scope(entity_type="manager")
        distributors = service.list_scope(entity_type="distributor")

    assert all("username" in item for item in managers)
    assert all(item.get("id") for item in managers)
    assert distributors == [] or all("username" in item for item in distributors)


def test_business_operator_routes_render(app):
    with app.test_client() as client:
        _auth_session(client)
        index = client.get("/admin/radius/business-operators")
        profile = client.get("/admin/radius/business-operators/manager/1")

    assert index.status_code == 200
    assert 'data-testid="manager-operators-table"' in index.get_data(as_text=True)
    assert profile.status_code == 200
    assert 'data-testid="operator-profit-summary"' in profile.get_data(as_text=True)
