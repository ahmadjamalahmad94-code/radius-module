from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "hotspot_cards_portal.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _plan(name: str = "Hotspot 8h") -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, quota_total_mb,
            speed_down_kbps, speed_up_kbps, price, currency, enabled,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 8 * 60, 1, 2048, 2048, 512, 5.0, "ILS", 1),
    )
    return int(cur.lastrowid)


def _package(*, name: str = "8 hours / 2 Mbps", active: int = 1, plan_id: int | None = None, price_minor: int = 500) -> int:
    plan = plan_id or _plan(name=f"{name} plan")
    cur = db().execute(
        """
        INSERT INTO card_marketplace_packages(
            tenant_id, name, plan_id, duration_minutes, speed_down_kbps,
            speed_up_kbps, price_minor, currency, active, metadata_json,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, plan, 8 * 60, 2048, 512, price_minor, "ILS", active, "{}"),
    )
    return int(cur.lastrowid)


def _subscriber(username: str = "hotspot-user", password: str = "portal-pass", *, status: str = "enabled", expired: bool = False) -> int:
    expire = (datetime.utcnow() - timedelta(days=1) if expired else datetime.utcnow() + timedelta(days=30)).isoformat() + "Z"
    cur = db().execute(
        """
        INSERT INTO subscribers(
            tenant_id, username, password, user_type, service_type, full_name,
            mobile, status, expire_at, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, username, password, "subscriber", "Hotspot", "Portal User", "0590001111", status, expire),
    )
    return int(cur.lastrowid)


def _card_user(mobile: str = "0599990000", password: str = "card-pass", *, status: str = "active") -> int:
    cur = db().execute(
        """
        INSERT INTO card_users(
            tenant_id, display_name, mobile, email, status, metadata_json,
            password_hash, password_set_at, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Card Buyer", mobile, "", status, "{}", generate_password_hash(password), datetime.utcnow().isoformat() + "Z"),
    )
    return int(cur.lastrowid)


def _wallet(owner_type: str, owner_id: int, balance_minor: int = 1000) -> int:
    cur = db().execute(
        """
        INSERT INTO wallets(
            tenant_id, owner_type, owner_id, balance_minor, currency,
            metadata_json, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, owner_type, owner_id, balance_minor, "ILS", "{}"),
    )
    return int(cur.lastrowid)


def _login(client, username: str, password: str) -> str:
    res = client.post("/api/v1/hotspot/cards/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.get_data(as_text=True)
    data = res.get_json()
    assert data["ok"] is True
    return data["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_succeeds_for_subscriber_and_card_user_credentials(app, client):
    with app.app_context():
        sub_id = _subscriber()
        card_user_id = _card_user()
        _wallet("subscriber", sub_id, 1000)
        _wallet("card_user", card_user_id, 1000)

    sub_token = _login(client, "hotspot-user", "portal-pass")
    card_token = _login(client, "0599990000", "card-pass")

    assert sub_token
    assert card_token


def test_login_rejects_invalid_password(app, client):
    with app.app_context():
        _subscriber()

    res = client.post("/api/v1/hotspot/cards/login", json={"username": "hotspot-user", "password": "bad"})

    assert res.status_code == 401
    assert res.get_json()["error"] == "invalid_credentials"


def test_invalid_and_expired_tokens_are_rejected(app, client):
    with app.app_context():
        sub_id = _subscriber()
        _wallet("subscriber", sub_id, 1000)
    token = _login(client, "hotspot-user", "portal-pass")

    invalid = client.get("/api/v1/hotspot/cards/me", headers=_auth("bad-token"))
    assert invalid.status_code == 401
    assert invalid.get_json()["error"] == "token_required"

    with app.app_context():
        db().execute(
            "UPDATE hotspot_portal_tokens SET expires_at=?",
            ((datetime.utcnow() - timedelta(seconds=1)).isoformat() + "Z",),
        )
    expired = client.get("/api/v1/hotspot/cards/me", headers=_auth(token))
    assert expired.status_code == 401
    assert expired.get_json()["error"] == "token_expired"


def test_catalog_returns_only_available_purchasable_cards(app, client):
    with app.app_context():
        sub_id = _subscriber()
        _wallet("subscriber", sub_id, 1000)
        visible_id = _package(name="Visible card", active=1)
        _package(name="Hidden card", active=0)
    token = _login(client, "hotspot-user", "portal-pass")

    res = client.get("/api/v1/hotspot/cards/catalog", headers=_auth(token))

    assert res.status_code == 200
    items = res.get_json()["items"]
    assert [item["id"] for item in items] == [str(visible_id)]
    assert items[0]["available"] is True
    assert items[0]["currency"] == "ILS"


def test_purchase_rejects_insufficient_balance(app, client):
    with app.app_context():
        sub_id = _subscriber()
        _wallet("subscriber", sub_id, 100)
        package_id = _package(price_minor=500)
    token = _login(client, "hotspot-user", "portal-pass")

    res = client.post(
        "/api/v1/hotspot/cards/purchase",
        json={"catalog_item_id": str(package_id), "client_request_id": "too-poor"},
        headers=_auth(token),
    )

    assert res.status_code == 402
    assert res.get_json()["error"] == "insufficient_balance"


def test_purchase_deducts_wallet_once_creates_ledger_and_issues_exactly_one_card(app, client):
    with app.app_context():
        sub_id = _subscriber()
        _wallet("subscriber", sub_id, 1000)
        package_id = _package(price_minor=500)
    token = _login(client, "hotspot-user", "portal-pass")

    first = client.post(
        "/api/v1/hotspot/cards/purchase",
        json={"catalog_item_id": str(package_id), "client_request_id": "same-click"},
        headers=_auth(token),
    )
    second = client.post(
        "/api/v1/hotspot/cards/purchase",
        json={"catalog_item_id": str(package_id), "client_request_id": "same-click"},
        headers=_auth(token),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["purchase_id"] == second.get_json()["purchase_id"]
    with app.app_context():
        wallet = db().execute("SELECT * FROM wallets WHERE owner_type='subscriber' AND owner_id=?", (sub_id,)).fetchone()
        tx_count = db().execute("SELECT COUNT(*) AS c FROM wallet_transactions WHERE reference_type='hotspot_card_purchase'").fetchone()["c"]
        ledger = db().execute("SELECT * FROM ledger_entries WHERE reference_type='hotspot_card_purchase'").fetchone()
        card_count = db().execute("SELECT COUNT(*) AS c FROM cards").fetchone()["c"]
        purchase_count = db().execute("SELECT COUNT(*) AS c FROM hotspot_card_purchases").fetchone()["c"]

    assert wallet["balance_minor"] == 500
    assert tx_count == 1
    assert ledger["entry_type"] == "card_sale"
    assert card_count == 1
    assert purchase_count == 1


def test_my_cards_returns_only_current_users_purchases(app, client):
    with app.app_context():
        sub1 = _subscriber("buyer-one")
        sub2 = _subscriber("buyer-two")
        _wallet("subscriber", sub1, 1000)
        _wallet("subscriber", sub2, 1000)
        package_id = _package(price_minor=500)
    token1 = _login(client, "buyer-one", "portal-pass")
    token2 = _login(client, "buyer-two", "portal-pass")

    client.post("/api/v1/hotspot/cards/purchase", json={"catalog_item_id": package_id}, headers=_auth(token1))
    mine = client.get("/api/v1/hotspot/cards/my-cards", headers=_auth(token1))
    other = client.get("/api/v1/hotspot/cards/my-cards", headers=_auth(token2))

    assert len(mine.get_json()["items"]) == 1
    assert other.get_json()["items"] == []


def test_send_sms_rejects_another_users_purchase(app, client):
    with app.app_context():
        sub1 = _subscriber("sms-owner")
        sub2 = _subscriber("sms-other")
        _wallet("subscriber", sub1, 1000)
        _wallet("subscriber", sub2, 1000)
        package_id = _package(price_minor=500)
    token1 = _login(client, "sms-owner", "portal-pass")
    token2 = _login(client, "sms-other", "portal-pass")
    purchase = client.post("/api/v1/hotspot/cards/purchase", json={"catalog_item_id": package_id}, headers=_auth(token1)).get_json()

    res = client.post("/api/v1/hotspot/cards/send-sms", json={"purchase_id": purchase["purchase_id"]}, headers=_auth(token2))

    assert res.status_code == 403
    assert res.get_json()["error"] == "forbidden"


def test_send_sms_returns_sms_not_configured_for_owned_purchase(app, client):
    with app.app_context():
        sub_id = _subscriber("sms-user")
        _wallet("subscriber", sub_id, 1000)
        package_id = _package(price_minor=500)
    token = _login(client, "sms-user", "portal-pass")
    purchase = client.post("/api/v1/hotspot/cards/purchase", json={"catalog_item_id": package_id}, headers=_auth(token)).get_json()

    res = client.post("/api/v1/hotspot/cards/send-sms", json={"purchase_id": purchase["purchase_id"]}, headers=_auth(token))

    assert res.status_code == 503
    assert res.get_json()["error"] == "sms_not_configured"
    with app.app_context():
        attempts = db().execute("SELECT COUNT(*) AS c FROM hotspot_card_sms_attempts").fetchone()["c"]
    assert attempts == 1


def test_apis_return_json_errors_and_need_no_admin_session(app, client):
    with app.app_context():
        sub_id = _subscriber("json-user")
        _wallet("subscriber", sub_id, 1000)
    missing = client.get("/api/v1/hotspot/cards/me")
    token = _login(client, "json-user", "portal-pass")
    authed = client.get("/api/v1/hotspot/cards/me", headers=_auth(token))

    assert missing.status_code == 401
    assert missing.is_json
    assert missing.get_json()["error"] == "token_required"
    assert authed.status_code == 200
    assert authed.get_json()["ok"] is True
