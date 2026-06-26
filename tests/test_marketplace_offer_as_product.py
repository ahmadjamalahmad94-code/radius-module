# -*- coding: utf-8 -*-
"""Marketplace «offer = one product» (Option A):

  * an INSTANT purchase provisions the buyer's OWN unique subscriber credential
    (own connection/session/quota) WITHOUT minting a card per sale,
  * the two offer-detail tables are DISJOINT (a card is in stock OR sold, never
    both),
  * INVENTORY mode still works (claims a pre-made stock card),
  * the per-buyer credential actually authenticates (proof of "own connection").

Per-file isolation (fresh app/db)."""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db
    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mkt_offer_product.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


def _service():
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    return CardUsersMarketplaceService(tenant_id=1)


def _plan_id() -> int:
    cur = db().execute(
        """INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
               price, currency, created_at, updated_at)
           VALUES(1,?,?,?,?,?,datetime('now'),datetime('now'))""",
        ("MK Plan", 8 * 60, 1, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


def _ready_buyer(svc, *, mobile="0590000001", amount="20.00"):
    user = svc.create_card_user(display_name="Buyer", mobile=mobile)
    svc.recharge_wallet(card_user_id=user["id"], amount=amount, actor="qa")
    return user


def _offer(svc, *, mode="instant", price="5.00", name="Offer"):
    return svc.create_package(name=name, plan_id=_plan_id(), price=price,
                              duration_minutes=480, sale_mode=mode)


# ════════ Option A — instant purchase = own connection, no card minted ════════

def test_two_buyers_get_distinct_own_credentials(app):
    with app.app_context():
        svc = _service()
        offer = _offer(svc)
        b1 = _ready_buyer(svc, mobile="0590000001")
        b2 = _ready_buyer(svc, mobile="0590000002")
        p1 = svc.purchase_package(card_user_id=b1["id"], package_id=offer["id"], actor="qa")
        p2 = svc.purchase_package(card_user_id=b2["id"], package_id=offer["id"], actor="qa")
        # distinct, non-empty credentials → each buyer their own connection
        assert p1["cred_username"] and p2["cred_username"]
        assert p1["cred_username"] != p2["cred_username"]
        assert p1["subscriber_id"] != p2["subscriber_id"]
        # no cards minted at all for the offer
        n = db().execute(
            "SELECT COUNT(*) n FROM cards c JOIN card_batches b ON b.id=c.batch_id "
            "WHERE b.package_id=?", (offer["id"],)).fetchone()["n"]
        assert n == 0


def test_instant_credential_authenticates_as_own_connection(app):
    """The per-buyer credential is a real RADIUS principal — it authenticates,
    and a wrong password is rejected (proves an independent connection)."""
    with app.app_context():
        from app.radius.services.policy_engine import authorize, AuthRequest
        svc = _service()
        offer = _offer(svc)
        buyer = _ready_buyer(svc)
        p = svc.purchase_package(card_user_id=buyer["id"], package_id=offer["id"], actor="qa")
        ok = authorize(AuthRequest(tenant_id=1, username=p["cred_username"],
                                   password=p["cred_password"]))
        bad = authorize(AuthRequest(tenant_id=1, username=p["cred_username"],
                                    password="wrong-password"))
    assert ok.ok is True                                  # own connection authenticates
    assert bad.ok is False and bad.reason == "password_wrong"   # real credential check


# ════════ The two offer-detail tables are DISJOINT ════════

def test_instant_offer_tables_disjoint(app):
    """Instant offer: no stock, so the TOP «remaining stock» table is empty and
    the sale appears ONLY in the BOTTOM «purchases» table (zero overlap)."""
    with app.app_context():
        svc = _service()
        offer = _offer(svc)
        buyer = _ready_buyer(svc)
        p = svc.purchase_package(card_user_id=buyer["id"], package_id=offer["id"], actor="qa")
        stock = svc.offer_cards(offer["id"])        # TOP table
        sales = svc.purchases_file(offer["id"])     # BOTTOM table
    assert stock["total"] == 0                      # nothing in stock for an instant offer
    assert sales["total"] == 1                      # the sale is here
    assert sales["items"][0]["username"] == p["cred_username"]   # credential surfaced


def test_inventory_sold_card_moves_from_stock_to_sales(app):
    """Inventory offer: a card is in stock BEFORE sale (TOP only) and in sales
    AFTER sale (BOTTOM only) — it is never in both tables at once."""
    with app.app_context():
        svc = _service()
        offer = _offer(svc, mode="inventory")
        svc.add_inventory_stock(package_id=offer["id"], count=2, actor="qa")
        # before any sale: both cards are stock, no sales
        assert svc.offer_cards(offer["id"])["total"] == 2
        assert svc.purchases_file(offer["id"])["total"] == 0
        buyer = _ready_buyer(svc)
        svc.purchase_package(card_user_id=buyer["id"], package_id=offer["id"], actor="qa")
        stock = svc.offer_cards(offer["id"])
        sales = svc.purchases_file(offer["id"])
    # one card moved out of stock into sales — disjoint, no double-count
    assert stock["total"] == 1            # one card still unsold
    assert sales["total"] == 1            # one card sold
    # the sold card's username is NOT among the remaining-stock usernames
    stock_users = {it["username"] for it in stock["items"]}
    sold_user = sales["items"][0]["username"]
    assert sold_user not in stock_users


def test_inventory_mode_still_mints_stock_and_claims(app):
    """Inventory mode is preserved: stock is pre-generated into the offer's pool
    and a purchase claims a real card (card_id populated)."""
    with app.app_context():
        svc = _service()
        offer = _offer(svc, mode="inventory")
        res = svc.add_inventory_stock(package_id=offer["id"], count=3, actor="qa")
        assert res["added"] == 3
        buyer = _ready_buyer(svc)
        p = svc.purchase_package(card_user_id=buyer["id"], package_id=offer["id"], actor="qa")
        assert p["card_id"]                            # inventory sale DOES back a card
        card = db().execute("SELECT * FROM cards WHERE id=?", (p["card_id"],)).fetchone()
        assert card["purchase_id"] == p["id"]         # linked to the purchase
