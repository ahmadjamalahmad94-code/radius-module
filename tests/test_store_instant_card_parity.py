"""Router-store API parity for the instant (Option A) sale mode.

Regression guard for the three linked failures reported on the live «متجر
البطاقات الإلكتروني» (store.html → /api/v1/store/*):

  1. Buying an offer returned an EMPTY credential in the «تم الشراء بنجاح»
     modal (اسم المستخدم/كلمة المرور = «—»).
  2. «سجل المشتريات» listed the purchase, but
  3. «بطاقاتي» stayed empty («لا تملك بطاقات بعد»).

Root cause: migration 140 made the default INSTANT sale mode provision the
buyer's own subscriber (credential on the purchase row: cred_username /
cred_password / subscriber_id; card_id NULL) instead of minting a cards row.
The web portal was updated, but app/api/v1/store.py still read credentials
only via card_id and filtered `card_id IS NOT NULL` in /store/my-cards — so
instant purchases returned blank creds and never showed in «بطاقاتي».

These tests drive the real HTTP endpoints end-to-end.
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_parity_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _svc(tenant_id=1):
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService,
    )
    return CardUsersMarketplaceService(tenant_id=tenant_id)


def _plan_id() -> int:
    from app.radius.db.connection import db
    cur = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                 speed_down_kbps, price, currency,
                                 created_at, updated_at)
        VALUES(?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Plan " + secrets.token_hex(3), 8 * 60, 1, 2048, 5.0, "ILS"),
    )
    return int(cur.lastrowid)


def _make_buyer(app, client, *, mobile, funds="20.00"):
    """Create a store customer + fund their wallet; return (token, id)."""
    with app.app_context():
        user = _svc().create_card_user(
            display_name="زبون اختبار", mobile=mobile, password="pw1234")
        _svc().recharge_wallet(card_user_id=user["id"], amount=funds, actor="qa")
    res = client.post("/api/v1/store/login",
                      json={"mobile": mobile, "password": "pw1234"})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    return data["token"], int(data["card_user"]["id"])


def _auth(token):
    return {"Authorization": "Bearer " + token}


def _instant_package(app, **over):
    with app.app_context():
        return _svc().create_package(
            name=over.get("name", "بطاقة 8 ساعات"),
            plan_id=_plan_id(),
            price=over.get("price", "5.00"),
            sale_mode="instant",
        )


def _inventory_package(app, *, stock, **over):
    with app.app_context():
        pkg = _svc().create_package(
            name=over.get("name", "بطاقة مخزون"),
            plan_id=_plan_id(),
            price=over.get("price", "5.00"),
            sale_mode="inventory",
        )
        if stock:
            _svc().add_inventory_stock(package_id=pkg["id"], count=stock, actor="qa")
        return _svc().get_package(pkg["id"])


# ───────────────────────── instant sale mode ─────────────────────────


def test_instant_purchase_returns_real_credentials_in_modal(app, client):
    """The success modal must carry a REAL non-empty username + password."""
    pkg = _instant_package(app)
    token, _cuid = _make_buyer(app, client, mobile="0590000001")
    res = client.post("/api/v1/store/purchase", headers=_auth(token),
                      json={"package_id": pkg["id"]})
    assert res.status_code == 201, res.get_json()
    card = res.get_json()["data"]["card"]
    assert card.get("username"), "modal username must not be empty"
    assert card.get("password"), "modal password must not be empty"
    # instant mode mints a card in the offer's format (default digits-only)
    assert card["username"].isdigit()


def test_instant_purchase_appears_in_my_cards_and_history(app, client):
    """Same purchase must land in BOTH «بطاقاتي» and «سجل المشتريات»."""
    pkg = _instant_package(app)
    token, _cuid = _make_buyer(app, client, mobile="0590000002")
    buy = client.post("/api/v1/store/purchase", headers=_auth(token),
                      json={"package_id": pkg["id"]})
    assert buy.status_code == 201, buy.get_json()
    bought = buy.get_json()["data"]["card"]

    cards = client.get("/api/v1/store/my-cards", headers=_auth(token)).get_json()["data"]
    assert cards["total"] == 1, "بطاقاتي must not be empty for an instant sale"
    item = cards["items"][0]
    assert item["username"] == bought["username"]
    assert item["password"] == bought["password"]
    assert item["state"] == "unused" and item["can_login"] is True

    hist = client.get("/api/v1/store/purchases", headers=_auth(token)).get_json()["data"]
    assert hist["total"] == 1
    # both panels are backed by card_user_purchases → identical count
    assert hist["total"] == cards["total"]
    assert hist["items"][0]["card_username"] == bought["username"]


def test_instant_purchase_mints_card_not_subscriber_and_charges_once(app, client):
    """Model correction: a real CARD backs the sale (NOT a subscriber); wallet
    debited exactly the price once."""
    pkg = _instant_package(app, price="5.00")
    token, cuid = _make_buyer(app, client, mobile="0590000003", funds="20.00")
    res = client.post("/api/v1/store/purchase", headers=_auth(token),
                      json={"package_id": pkg["id"]})
    assert res.status_code == 201, res.get_json()
    username = res.get_json()["data"]["card"]["username"]
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import cards_repo
        # a real card exists…
        card = cards_repo.get_card_by_username(1, username)
        assert card is not None, "instant sale must mint a real card"
        assert card.plan_id == pkg["plan_id"]
        # …and NO permanent subscriber row is created for it.
        n_sub = db().execute(
            "SELECT COUNT(*) n FROM subscribers WHERE tenant_id=1 AND username=?",
            (username,)).fetchone()["n"]
        assert int(n_sub) == 0, "instant sale must NOT create a subscribers row"
        wb = db().execute(
            "SELECT balance_minor FROM wallets "
            "WHERE owner_type='card_user' AND owner_id=?", (cuid,)).fetchone()
        # funded 20.00, one 5.00 purchase → 15.00 left (no double-charge)
        assert int(wb["balance_minor"]) == 1500


# ───────────────────────── inventory sale mode ─────────────────────────


def test_inventory_purchase_claims_real_card_and_decrements_stock(app, client):
    pkg = _inventory_package(app, stock=2)
    token, _cuid = _make_buyer(app, client, mobile="0590000010")
    res = client.post("/api/v1/store/purchase", headers=_auth(token),
                      json={"package_id": pkg["id"]})
    assert res.status_code == 201, res.get_json()
    card = res.get_json()["data"]["card"]
    assert card.get("username") and card.get("password")
    assert not card["username"].startswith("mk")  # a claimed stock card, not a mint

    cards = client.get("/api/v1/store/my-cards", headers=_auth(token)).get_json()["data"]
    assert cards["total"] == 1
    assert cards["items"][0]["username"] == card["username"]

    with app.app_context():
        pkg2 = _svc().get_package(pkg["id"])
        assert pkg2["inventory_sold"] == 1
        assert pkg2["inventory_total"] - pkg2["inventory_sold"] == 1


def test_out_of_stock_returns_clear_error_no_blank_card_no_charge(app, client):
    pkg = _inventory_package(app, stock=0)  # empty inventory
    token, cuid = _make_buyer(app, client, mobile="0590000011", funds="20.00")
    res = client.post("/api/v1/store/purchase", headers=_auth(token),
                      json={"package_id": pkg["id"]})
    assert res.status_code == 422, res.get_json()
    assert "نفد" in res.get_json()["error"]["message"]

    # no purchase row, no blank card, wallet untouched
    cards = client.get("/api/v1/store/my-cards", headers=_auth(token)).get_json()["data"]
    assert cards["total"] == 0
    hist = client.get("/api/v1/store/purchases", headers=_auth(token)).get_json()["data"]
    assert hist["total"] == 0
    with app.app_context():
        from app.radius.db.connection import db
        wb = db().execute(
            "SELECT balance_minor FROM wallets "
            "WHERE owner_type='card_user' AND owner_id=?", (cuid,)).fetchone()
        assert int(wb["balance_minor"]) == 2000  # 20.00 intact — never charged
