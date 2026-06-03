"""QA: electronic-cards sale modes — instant vs inventory (migration 095).

Covers the inventory path added in feature/ecards-purchases-and-packages:
stock add (generate + import), atomic claim with O(1) counters, out-of-stock
safety (no charge), section-wide default + per-offer override.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db
    return live_db()


def _svc():
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    return CardUsersMarketplaceService


def _err():
    from app.radius.services.card_users_marketplace import CardMarketplaceError
    return CardMarketplaceError


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "ecards_inventory.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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
    flask_app.config["_DBF"] = db_file
    return flask_app


import secrets


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                 price, currency, created_at, updated_at)
        VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Inv Plan " + secrets.token_hex(3), 8 * 60, 1, 5.0, "ILS"),
    )
    return int(cur.lastrowid)


def _inventory_package(service, **over):
    return service.create_package(
        name=over.get("name", "Booth 8h"),
        plan_id=_plan_id(),
        price=over.get("price", "5.00"),
        sale_mode=over.get("sale_mode", "inventory"),
    )


def test_create_inventory_package_and_add_generated_stock(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)
        assert pkg["sale_mode"] == "inventory"
        res = s.add_inventory_stock(package_id=pkg["id"], count=5, actor="qa")
        assert res["added"] == 5
        pkg = s.get_package(pkg["id"])
        assert pkg["inventory_total"] == 5 and pkg["inventory_sold"] == 0
        # stock cards are linked to the offer and in stock (purchase_id NULL)
        rows = db().execute(
            "SELECT COUNT(*) n FROM cards c JOIN card_batches b ON b.id=c.batch_id "
            "WHERE b.package_id=? AND c.purchase_id IS NULL", (pkg["id"],)).fetchone()
        assert rows["n"] == 5


def test_inventory_purchase_claims_stock_and_bumps_counters(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)
        s.add_inventory_stock(package_id=pkg["id"], count=3, actor="qa")
        user = s.create_card_user(display_name="Booth Buyer", mobile="059")
        s.recharge_wallet(card_user_id=user["id"], amount="10.00", actor="qa")
        purchase = s.purchase_package(card_user_id=user["id"], package_id=pkg["id"], actor="qa")
        card = db().execute("SELECT * FROM cards WHERE id=?", (purchase["card_id"],)).fetchone()
        # claimed a real stock card (not an instant 'mp' mint), linked to the purchase
        assert not card["username"].startswith("mp")
        assert int(card["purchase_id"]) == int(purchase["id"])
        pkg = s.get_package(pkg["id"])
        assert pkg["inventory_sold"] == 1
        assert pkg["inventory_total"] - pkg["inventory_sold"] == 2  # remaining


def test_inventory_out_of_stock_raises_without_charging(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)  # zero stock
        user = s.create_card_user(display_name="No Stock", mobile="059")
        s.recharge_wallet(card_user_id=user["id"], amount="10.00", actor="qa")
        before = s.get_card_user(user["id"])
        with pytest.raises(_err(), match="نفد مخزون"):
            s.purchase_package(card_user_id=user["id"], package_id=pkg["id"], actor="qa")
        # wallet was NOT charged (out-of-stock check precedes the debit)
        wb = db().execute(
            "SELECT balance_minor FROM wallets WHERE owner_type='card_user' AND owner_id=?",
            (user["id"],)).fetchone()
        assert int(wb["balance_minor"]) == 1000  # 10.00 intact


def test_atomic_claim_prevents_double_sell(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)
        s.add_inventory_stock(package_id=pkg["id"], count=2, actor="qa")
        seen = set()
        for i in range(2):
            u = s.create_card_user(display_name=f"B{i}", mobile="059")
            s.recharge_wallet(card_user_id=u["id"], amount="10.00", actor="qa")
            p = s.purchase_package(card_user_id=u["id"], package_id=pkg["id"], actor="qa")
            seen.add(int(p["card_id"]))
        assert len(seen) == 2  # two distinct cards, never the same one
        u3 = s.create_card_user(display_name="B3", mobile="059")
        s.recharge_wallet(card_user_id=u3["id"], amount="10.00", actor="qa")
        with pytest.raises(_err(), match="نفد مخزون"):
            s.purchase_package(card_user_id=u3["id"], package_id=pkg["id"], actor="qa")


def test_import_rows_as_stock(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)
        rows = [{"username": "card001", "password": "111"},
                {"username": "card002", "password": "222"}]
        res = s.add_inventory_stock(package_id=pkg["id"], cards=rows, actor="qa")
        assert res["added"] == 2
        got = {r["username"] for r in db().execute(
            "SELECT username FROM cards c JOIN card_batches b ON b.id=c.batch_id "
            "WHERE b.package_id=?", (pkg["id"],)).fetchall()}
        assert {"card001", "card002"} <= got


def test_purchases_file_paginated_with_detail(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)
        s.add_inventory_stock(package_id=pkg["id"], count=3, actor="qa")
        for i in range(2):
            u = s.create_card_user(display_name=f"Buyer{i}", mobile="059")
            s.recharge_wallet(card_user_id=u["id"], amount="10.00", actor="qa")
            s.purchase_package(card_user_id=u["id"], package_id=pkg["id"], actor="qa")
        f = s.purchases_file(pkg["id"], page=1, per_page=1)
        assert f["total"] == 2 and f["pages"] == 2 and len(f["items"]) == 1
        assert f["sold"] == 2 and f["remaining"] == 1 and f["stock_total"] == 3
        item = f["items"][0]
        for k in ("username", "password", "buyer_name", "amount_minor", "status",
                  "created_at", "download_bytes", "upload_bytes"):
            assert k in item
        assert item["buyer_name"] in {"Buyer0", "Buyer1"}


def test_recent_purchases_global_panel(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s, name="Booth A")
        s.add_inventory_stock(package_id=pkg["id"], count=1, actor="qa")
        u = s.create_card_user(display_name="GBuyer", mobile="059")
        s.recharge_wallet(card_user_id=u["id"], amount="10.00", actor="qa")
        s.purchase_package(card_user_id=u["id"], package_id=pkg["id"], actor="qa")
        r = s.recent_purchases(page=1, per_page=10)
        assert r["total"] == 1 and len(r["items"]) == 1
        assert r["items"][0]["package_name"] == "Booth A"
        assert r["items"][0]["buyer_name"] == "GBuyer"


def _bind(app):
    from app.radius.db.connection import reset_for_tests
    os.environ["HOBERADIUS_DB_PATH"] = app.config["_DBF"]
    reset_for_tests(app.config["_DBF"])


def _login(client):
    with client.session_transaction() as sess:
        sess.update(admin_id=1, admin_user="a", admin_name="A",
                    is_super_admin=True, tenant_id=1, _csrf_token="t")


def test_purchases_file_and_marketplace_pages_render(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)
        s.add_inventory_stock(package_id=pkg["id"], count=2, actor="qa")
        u = s.create_card_user(display_name="PgBuyer", mobile="059")
        s.recharge_wallet(card_user_id=u["id"], amount="10.00", actor="qa")
        s.purchase_package(card_user_id=u["id"], package_id=pkg["id"], actor="qa")
        pid = pkg["id"]
    _bind(app)
    client = app.test_client()
    _login(client)
    r = client.get(f"/admin/radius/card-marketplace/packages/{pid}/file")
    assert r.status_code == 200, r.status_code
    html = r.get_data(as_text=True)
    assert "ملف مشتريات" in html and "PgBuyer" in html
    _bind(app)
    _login(client)
    assert client.get("/admin/radius/card-marketplace").status_code == 200


def test_inventory_generate_via_route(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)
        pid = pkg["id"]
    _bind(app)
    client = app.test_client()
    _login(client)
    r = client.post(
        f"/admin/radius/card-marketplace/packages/{pid}/inventory",
        data={"count": "4", "_csrf_token": "t"}, follow_redirects=False,
    )
    assert r.status_code in {302, 303}
    _bind(app)
    with app.app_context():
        pkg = _svc()(tenant_id=1).get_package(pid)
        assert pkg["inventory_total"] == 4


def test_inventory_file_import_via_route_reuses_engine(app):
    import io as _io
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = _inventory_package(s)
        pid = pkg["id"]
    _bind(app)
    client = app.test_client()
    _login(client)
    # a CSV the shared cards_import_engine parses into username/password rows
    csv = b"username,password\nimpuser1,pw1\nimpuser2,pw2\nimpuser3,pw3\n"
    r = client.post(
        f"/admin/radius/card-marketplace/packages/{pid}/inventory",
        data={"file": (_io.BytesIO(csv), "stock.csv"), "_csrf_token": "t"},
        content_type="multipart/form-data", follow_redirects=False,
    )
    assert r.status_code in {302, 303}
    _bind(app)
    with app.app_context():
        s = _svc()(tenant_id=1)
        pkg = s.get_package(pid)
        assert pkg["inventory_total"] == 3
        got = {row["username"] for row in db().execute(
            "SELECT c.username FROM cards c JOIN card_batches b ON b.id=c.batch_id "
            "WHERE b.package_id=?", (pid,)).fetchall()}
        assert {"impuser1", "impuser2", "impuser3"} <= got


def test_section_default_mode_and_per_offer_override(app):
    with app.app_context():
        s = _svc()(tenant_id=1)
        s.set_default_sale_mode("inventory")
        inherited = s.create_package(name="Inherits", plan_id=_plan_id(), price="3.00")
        assert inherited["sale_mode"] == "inventory"          # section default
        override = s.create_package(name="Override", plan_id=_plan_id(), price="3.00",
                                    sale_mode="instant")
        assert override["sale_mode"] == "instant"             # per-offer override
        flipped = s.set_package_sale_mode(inherited["id"], "instant")
        assert flipped["sale_mode"] == "instant"
