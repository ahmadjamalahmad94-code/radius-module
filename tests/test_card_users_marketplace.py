from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.services.card_users_marketplace import (
    CardMarketplaceError,
    CardUsersMarketplaceService,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "card_users_marketplace.db")
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
        sess["admin_user"] = "card_admin"
        sess["admin_name"] = "Card Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "card-csrf"


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Marketplace 8h", 8 * 60, 1, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


def _market(app):
    with app.app_context():
        service = CardUsersMarketplaceService(tenant_id=1)
        user = service.create_card_user(display_name="Walk-in Buyer", mobile="0590000000")
        package = service.create_package(
            name="8 hours / 2 Mbps",
            plan_id=_plan_id(),
            duration_minutes=8 * 60,
            speed_down_kbps=2048,
            speed_up_kbps=512,
            price="5.00",
        )
    return user, package


def test_wallet_purchase_flow_assigns_card_and_records_finance(app):
    user, package = _market(app)
    with app.app_context():
        service = CardUsersMarketplaceService(tenant_id=1)
        service.recharge_wallet(card_user_id=user["id"], amount="10.00", actor="qa")
        purchase = service.purchase_package(
            card_user_id=user["id"],
            package_id=package["id"],
            actor="qa",
        )
        card = db().execute("SELECT * FROM cards WHERE id=?", (purchase["card_id"],)).fetchone()
        ledger = db().execute(
            "SELECT * FROM ledger_entries WHERE reference_type='card_user_purchase' AND reference_id=?",
            (purchase["id"],),
        ).fetchone()
        revenue = db().execute(
            "SELECT * FROM revenue_records WHERE source_type='card_user_purchase' AND source_id=?",
            (purchase["id"],),
        ).fetchone()
        event = db().execute(
            "SELECT * FROM business_events WHERE event_key='card_user.card_purchased' AND target_id=?",
            (user["id"],),
        ).fetchone()
        card360 = service.card_user_360(user["id"])

    assert purchase["status"] == "completed"
    assert card is not None
    assert card["username"].startswith("mp")
    assert ledger["entry_type"] == "card_sale"
    assert revenue["collected_amount_minor"] == 500
    assert event is not None
    assert card360["cards"][0]["id"] == purchase["card_id"]


def test_purchase_blocks_insufficient_balance(app):
    user, package = _market(app)
    with app.app_context():
        service = CardUsersMarketplaceService(tenant_id=1)
        with pytest.raises(CardMarketplaceError, match="insufficient"):
            service.purchase_package(card_user_id=user["id"], package_id=package["id"], actor="qa")


def test_route_smoke_for_card_users_and_marketplace(app):
    user, package = _market(app)
    with app.test_client() as client:
        _auth_session(client)
        users_res = client.get("/admin/radius/card-users")
        detail_res = client.get(f"/admin/radius/card-users/{user['id']}")
        market_res = client.get("/admin/radius/card-marketplace")

    assert users_res.status_code == 200
    assert detail_res.status_code == 200
    assert market_res.status_code == 200
    assert "Walk-in Buyer" in users_res.get_data(as_text=True)
    assert "Card User 360" in detail_res.get_data(as_text=True)
    assert package["name"] in market_res.get_data(as_text=True)


def test_web_purchase_action_deducts_wallet(app):
    user, package = _market(app)
    with app.app_context():
        CardUsersMarketplaceService(tenant_id=1).recharge_wallet(
            card_user_id=user["id"],
            amount="5.00",
            actor="qa",
        )

    with app.test_client() as client:
        _auth_session(client)
        res = client.post(
            f"/admin/radius/card-users/{user['id']}/purchase",
            data={"_csrf_token": "card-csrf", "package_id": package["id"]},
            follow_redirects=True,
        )
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "owned-cards-table" in html
    with app.app_context():
        wallet = CardUsersMarketplaceService(tenant_id=1).card_user_360(user["id"])["wallet"]
    assert wallet["balance"] == "0.00"


def test_marketplace_does_not_touch_live_radius(app):
    user, package = _market(app)
    with app.app_context():
        service = CardUsersMarketplaceService(tenant_id=1)
        service.recharge_wallet(card_user_id=user["id"], amount="5.00", actor="qa")
        purchase = service.purchase_package(card_user_id=user["id"], package_id=package["id"])
        radius_actions = db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='radius_activation_actions'"
        ).fetchone()

    assert purchase["delivery_status"] == "event_only"
    if radius_actions:
        with app.app_context():
            count = db().execute("SELECT COUNT(*) AS c FROM radius_activation_actions").fetchone()["c"]
        assert count == 0
