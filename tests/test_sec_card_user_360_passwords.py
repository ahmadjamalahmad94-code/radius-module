"""SEC-360 (card users): ``GET /api/v1/card-users/<id>/360`` returned the
plaintext passwords of every card the card-user bought — ``cards[].password``
(the raw ``cards`` row / the instant-sale credential) and
``purchases[].cred_password`` (the raw ``card_user_purchases`` row) — to any
admin holding ``store.view``, with no card-password permission check.

Web parity: the web 360 page never renders card passwords; every web page
that does (batch cards, the offer's purchases file, the cards checker) uses
``can_view_card_passwords`` = owner / co-owner / ``scope.view_passwords`` /
``cards.print``. The API now masks with the same rule; field names stay (the
Flutter app shows ``••••`` + «لا تملك صلاحية كشفها» for a masked value).
"""
from __future__ import annotations

import os
import uuid

import pytest

MASK = "••••••"
PW = "Pass-12345"
CARD_PW = "CardSecret-71aa"
INSTANT_PW = "InstantSecret-3c9d"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "sec_cu360.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password=PW,
                                 full_name="Owner", is_super_admin=True,
                                 role_id=admins_repo.get_role_by_name("super_admin").id)
    return flask_app


def _db():
    from app.radius.db.connection import db
    return db()


def _seed() -> int:
    """A card user with one inventory card (cards row) + one instant sale."""
    conn = _db()
    plan = conn.execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, price, currency,"
        " created_at, updated_at) VALUES(1, ?, 60, 5.0, 'ILS', datetime('now'),"
        " datetime('now'))", ("p-" + uuid.uuid4().hex[:6],)).lastrowid
    pkg = conn.execute(
        "INSERT INTO card_marketplace_packages(tenant_id, name, plan_id, price_minor,"
        " currency, created_at) VALUES(1, 'pkg', ?, 500, 'ILS', datetime('now'))",
        (plan,)).lastrowid
    cu = conn.execute(
        "INSERT INTO card_users(tenant_id, display_name, mobile, created_at)"
        " VALUES(1, 'buyer', '0599000111', datetime('now'))").lastrowid
    batch = conn.execute(
        "INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id, count,"
        " generated, created_at) VALUES(1, ?, 'Cards', ?, 1, 1, datetime('now'))",
        ("B-" + uuid.uuid4().hex[:6], plan)).lastrowid
    card = conn.execute(
        "INSERT INTO cards(tenant_id, batch_id, plan_id, username, password, used,"
        " created_at) VALUES(1, ?, ?, 'card-inv-1', ?, 0, datetime('now'))",
        (batch, plan, CARD_PW)).lastrowid
    conn.execute(
        "INSERT INTO card_user_purchases(tenant_id, card_user_id, package_id, card_id,"
        " amount_minor, currency, created_at) VALUES(1, ?, ?, ?, 500, 'ILS',"
        " datetime('now'))", (cu, pkg, card))
    conn.execute(
        "INSERT INTO card_user_purchases(tenant_id, card_user_id, package_id,"
        " amount_minor, currency, created_at, cred_username, cred_password)"
        " VALUES(1, ?, ?, 500, 'ILS', datetime('now'), 'inst-1', ?)",
        (cu, pkg, INSTANT_PW))
    conn.commit()
    return int(cu)


def _manager(perms) -> dict:
    from app.radius.db.repos import admins_repo, api_tokens_repo
    role = admins_repo.create_role(name="r_" + uuid.uuid4().hex[:8], display_name="R",
                                   permissions=tuple(perms))
    admin = admins_repo.create_admin(username="m_" + uuid.uuid4().hex[:8], password=PW,
                                     full_name="M", is_super_admin=False, role_id=role.id)
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name="t-" + uuid.uuid4().hex[:6], scopes=["admin:full"],
        created_by=int(admin.id))
    return {"Authorization": "Bearer " + plain}


def _owner(client) -> dict:
    res = client.post("/api/admin/login", json={"username": "owner_root", "password": PW})
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


def _get360(app, headers):
    with app.app_context():
        cu = _seed()
    client = app.test_client()
    hdr = headers(client) if callable(headers) else headers
    res = client.get(f"/api/v1/card-users/{cu}/360", headers=hdr)
    assert res.status_code == 200, res.get_json()
    return res


def test_store_viewer_without_card_password_permission_gets_masks(app):
    with app.app_context():
        hdr = _manager(["store.view"])
    res = _get360(app, hdr)
    raw = res.get_data(as_text=True)
    assert CARD_PW not in raw
    assert INSTANT_PW not in raw
    data = res.get_json()["data"]
    cards = data["cards"]
    assert len(cards) == 2
    # Keys kept for the app; value is the mask (app renders «••••»).
    assert all(c["password"] == MASK for c in cards)
    assert {c["username"] for c in cards} == {"card-inv-1", "inst-1"}
    instant = [p for p in data["purchases"] if p.get("cred_username") == "inst-1"]
    assert instant and instant[0]["cred_password"] == MASK


@pytest.mark.parametrize("perm", ["cards.print", "scope.view_passwords"])
def test_card_password_permission_still_sees_passwords(app, perm):
    with app.app_context():
        hdr = _manager(["store.view", perm])
    data = _get360(app, hdr).get_json()["data"]
    assert {c["password"] for c in data["cards"]} == {CARD_PW, INSTANT_PW}
    instant = [p for p in data["purchases"] if p.get("cred_username") == "inst-1"]
    assert instant[0]["cred_password"] == INSTANT_PW


def test_owner_sees_passwords(app):
    data = _get360(app, _owner).get_json()["data"]
    assert {c["password"] for c in data["cards"]} == {CARD_PW, INSTANT_PW}


def test_purchase_response_masks_credential_without_permission(app):
    with app.app_context():
        cu = _seed()
        pkg = int(_db().execute(
            "SELECT id FROM card_marketplace_packages ORDER BY id LIMIT 1").fetchone()[0])
        hdr = _manager(["store.view", "store.user_recharge", "store.user_purchase"])
    client = app.test_client()
    res = client.post(f"/api/v1/card-users/{cu}/recharge", json={"amount": 20},
                      headers=hdr)
    assert res.status_code == 201, res.get_json()
    res = client.post(f"/api/v1/card-users/{cu}/purchase", json={"package_id": pkg},
                      headers=hdr)
    assert res.status_code == 201, res.get_json()
    purchase = res.get_json()["data"]["purchase"]
    assert purchase.get("cred_username")
    assert purchase["cred_password"] == MASK
    with app.app_context():
        real = _db().execute("SELECT cred_password FROM card_user_purchases WHERE id=?",
                             (purchase["id"],)).fetchone()[0]
    assert real and real != MASK
