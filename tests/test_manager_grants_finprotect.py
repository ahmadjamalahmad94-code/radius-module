"""Stage B — financial protection.

  • can_see_wholesale (default OFF) → the manager does NOT see the wholesale /
    cost price nor the margin. Enforced by SERVER-SIDE projection (the value is
    stripped before the template, not hidden by CSS).
  • subscriber «price» field (custom_price) added to the field registry → the
    owner can lock a manager out of changing the price (field-control revert).

Owner/super always sees everything. The approval-queue item is a separate stage.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_finprotect.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
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
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _mgr(username="m1") -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", is_super_admin=False)
    return int(adm.id)


def _plan():
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,price,currency,created_at,updated_at)"
        " VALUES(1,'P',60,5.0,'JOD',datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


def _offer_visible_to(mgr):
    from app.radius.services.card_offers import CardOffersService
    svc = CardOffersService(tenant_id=1)
    off = svc.create_offer(name="Off", duration_minutes=60, wholesale=7.77,
                           selling=9.99, plan_id=_plan(), created_by="owner")
    svc.set_visibility(int(off["id"]), [mgr])
    return off


def _grant_see_wholesale(mgr, val=True):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr, permissions={"can_see_wholesale": val})


def _login(client, *, admin_id, is_super, perms=("cards.view",)):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


# ═══ visibility default + helper ════════════════════════════════════════════
def test_can_see_wholesale_default_off(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def")
        assert mg.can_see(m, "can_see_wholesale", tenant_id=1) is False
    with app.app_context():
        _grant_see_wholesale(m, True)
    with app.app_context():
        assert mg.can_see(m, "can_see_wholesale", tenant_id=1) is True


# ═══ server-side projection on the offers page ══════════════════════════════
def test_wholesale_hidden_from_manager_without_grant(app):
    with app.app_context():
        m = _mgr("m_hide"); _offer_visible_to(m)   # wholesale 7.77, selling 9.99
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        html = c.get("/admin/radius/cards/offers").get_data(as_text=True)
    assert "9.99" in html            # selling IS shown
    assert "7.77" not in html        # wholesale/cost is STRIPPED server-side


def test_wholesale_visible_to_manager_with_grant(app):
    with app.app_context():
        m = _mgr("m_show"); _offer_visible_to(m); _grant_see_wholesale(m, True)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        html = c.get("/admin/radius/cards/offers").get_data(as_text=True)
    assert "7.77" in html            # granted → wholesale shown


def test_wholesale_visible_to_super(app):
    with app.app_context():
        m = _mgr("m_sup"); _offer_visible_to(m)
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        html = c.get("/admin/radius/cards/offers").get_data(as_text=True)
    assert "7.77" in html


# ═══ subscriber price-edit lock (field registry) ════════════════════════════
def test_subscriber_price_field_registered(app):
    from app.radius.services import manager_grants as mg
    assert "price" in mg.field_keys("subscriber")
    # field-control granting only «name» reverts custom_price
    with app.app_context():
        m = _mgr("m_price")
        mg.set_field_grants(m, "subscriber", ["name"], tenant_id=1)
        assert "custom_price" in mg.reverted_attrs(m, "subscriber", tenant_id=1)
        mg.set_field_grants(m, "subscriber", ["name", "price"], tenant_id=1)
    with app.app_context():
        assert "custom_price" not in mg.reverted_attrs(m, "subscriber", tenant_id=1)


# ═══ config persists ════════════════════════════════════════════════════════
def test_policy_route_persists_can_see_wholesale(app):
    with app.app_context():
        m = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf", "can_see_wholesale": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.can_see(m, "can_see_wholesale", tenant_id=1) is True
