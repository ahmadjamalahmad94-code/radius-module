from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.services.card_users_marketplace import (
    CardMarketplaceError,
    CardUsersMarketplaceService,
)

AUTH = {"Authorization": "Bearer dev-token-please-change"}


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
    assert "Walk-in Buyer" in detail_res.get_data(as_text=True)
    assert "card-user-purchase-form" in detail_res.get_data(as_text=True)
    assert package["name"] in market_res.get_data(as_text=True)


def test_card_users_api_contract_hides_portal_password_hash(app):
    with app.test_client() as client:
        created = client.post(
            "/api/v1/card-users",
            json={
                "display_name": "Mobile Buyer",
                "mobile": "0591111111",
                "email": "buyer@example.test",
                "password": "1234",
            },
            headers=AUTH,
        )
        listed = client.get("/api/v1/card-users", headers=AUTH)

    assert created.status_code == 201
    payload = created.get_json()["data"]["card_user"]
    assert payload["display_name"] == "Mobile Buyer"
    assert payload["has_portal_password"] is True
    assert "password_hash" not in payload

    assert listed.status_code == 200
    item = listed.get_json()["data"]["items"][0]
    assert "password_hash" not in item
    assert listed.get_json()["data"]["summary"]["users"] == 1


def test_card_users_api_recharge_purchase_and_360(app):
    with app.app_context():
        plan_id = _plan_id()

    with app.test_client() as client:
        user_res = client.post(
            "/api/v1/card-users",
            json={"display_name": "Portal Buyer", "mobile": "0592222222"},
            headers=AUTH,
        )
        package_res = client.post(
            "/api/v1/card-marketplace/packages",
            json={
                "name": "باقة 8 ساعات",
                "plan_id": plan_id,
                "price": "5.00",
                "duration_minutes": 480,
                "speed_down_kbps": 2048,
                "speed_up_kbps": 512,
                "currency": "ILS",
            },
            headers=AUTH,
        )
        user_id = user_res.get_json()["data"]["card_user"]["id"]
        package_id = package_res.get_json()["data"]["package"]["id"]

        recharge = client.post(
            f"/api/v1/card-users/{user_id}/recharge",
            json={"amount": "5.00"},
            headers=AUTH,
        )
        purchase = client.post(
            f"/api/v1/card-users/{user_id}/purchase",
            json={"package_id": package_id},
            headers=AUTH,
        )
        profile = client.get(f"/api/v1/card-users/{user_id}/360", headers=AUTH)

    assert user_res.status_code == 201
    assert package_res.status_code == 201
    assert recharge.status_code == 201
    assert purchase.status_code == 201
    assert profile.status_code == 200
    data = profile.get_json()["data"]
    assert data["card_user"]["display_name"] == "Portal Buyer"
    assert "password_hash" not in data["card_user"]
    assert data["wallet"]["balance"] == "0.00"
    assert data["purchases"][0]["status"] == "completed"
    assert data["cards"][0]["username"].startswith("mp")
    assert data["messages"][0]["message"] == "لم يتم ربط مزود الرسائل بعد."


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
    assert "mp" in html
    assert "u360-status--fresh" in html
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
    assert purchase["metadata"]["message_delivery"] == "event_recorded"
    if radius_actions:
        with app.app_context():
            count = db().execute("SELECT COUNT(*) AS c FROM radius_activation_actions").fetchone()["c"]
        assert count == 0


def test_card_user_360_messages_are_arabic_and_not_placeholder(app):
    user, package = _market(app)
    with app.app_context():
        service = CardUsersMarketplaceService(tenant_id=1)
        service.recharge_wallet(card_user_id=user["id"], amount="5.00", actor="qa")
        service.purchase_package(card_user_id=user["id"], package_id=package["id"])
        card_user = service.card_user_360(user["id"])

    message = card_user["messages"][0]
    assert message["status"] == "event_recorded"
    assert "تم تسجيل" in message["message"]
    assert "placeholder" not in message["message"].lower()
