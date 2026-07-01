"""C2 — balance / profit visibility (server-side projection).

  • can_see_profit  (default OFF) → the offer margin/profit is stripped server-
    side from the offers page (value not in the response).
  • can_see_balance (default OFF) → the balance/financial KPIs are not rendered
    on the subscriber-360 page (replaced by «محجوب»; value not in the response).

Owner/super always sees. Block-test each.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_datavis2.db")
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


def _mgr(username) -> int:
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
    svc.set_visibility(int(off["id"]), [mgr])   # margin = 2.22
    return off


def _sub(username, mgr):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    s = subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="p1234567",
        status="enabled", manager_id=mgr))
    return int(s.id)


def _grant(mgr, flag, val=True):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr, permissions={flag: val})


def _login(client, *, admin_id, is_super, perms=("cards.view", "users.view")):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


# ═══ defaults ═══════════════════════════════════════════════════════════════
def test_visibility_keys_default_off(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def")
        assert mg.can_see(m, "can_see_balance", tenant_id=1) is False
        assert mg.can_see(m, "can_see_profit", tenant_id=1) is False


# ═══ profit (offer margin) ══════════════════════════════════════════════════
def test_profit_hidden_without_grant(app):
    with app.app_context():
        m = _mgr("m_np"); _offer_visible_to(m)
        _grant(m, "can_see_wholesale", True)   # cost shown, but profit NOT
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        html = c.get("/admin/radius/cards/offers").get_data(as_text=True)
    assert "7.77" in html          # wholesale shown (can_see_wholesale on)
    assert "2.22" not in html      # margin/profit STRIPPED (no can_see_profit)


def test_profit_visible_with_grant(app):
    with app.app_context():
        m = _mgr("m_p"); _offer_visible_to(m)
        _grant(m, "can_see_wholesale", True)
    with app.app_context():
        _grant(m, "can_see_profit", True)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        html = c.get("/admin/radius/cards/offers").get_data(as_text=True)
    assert "2.22" in html


# ═══ balance (subscriber-360) ═══════════════════════════════════════════════
def test_balance_hidden_without_grant(app):
    with app.app_context():
        m = _mgr("m_nb"); sid = _sub("bsub", m)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        html = c.get(f"/admin/radius/subscribers/{sid}").get_data(as_text=True)
    assert "رصيد المحفظة" not in html    # balance KPI not rendered
    assert "محجوب" in html


def test_balance_visible_with_grant(app):
    with app.app_context():
        m = _mgr("m_b"); sid = _sub("bsub2", m); _grant(m, "can_see_balance", True)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        html = c.get(f"/admin/radius/subscribers/{sid}").get_data(as_text=True)
    assert "رصيد المحفظة" in html


def test_balance_visible_to_super(app):
    with app.app_context():
        m = _mgr("m_bs"); sid = _sub("bsub3", m)
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        html = c.get(f"/admin/radius/subscribers/{sid}").get_data(as_text=True)
    assert "رصيد المحفظة" in html


# ═══ config persists ════════════════════════════════════════════════════════
def test_policy_persists_datavis2(app):
    with app.app_context():
        m = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf", "can_see_balance": "1",
                         "can_see_profit": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.can_see(m, "can_see_balance", tenant_id=1) is True
        assert mg.can_see(m, "can_see_profit", tenant_id=1) is True
