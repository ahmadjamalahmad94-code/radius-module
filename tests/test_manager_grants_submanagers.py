"""F3 — sub-managers + delegation ceiling.

A manager granted can_create_sub_managers may create sub-managers UNDER him and
delegate only a SUBSET of his OWN permissions (never more — server-enforced
clamp). Creation is gated; delegated grants ⊆ parent's; escalation is dropped.
Owner/super bypass.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_sub.db")
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


def _policy(mgr, *, permissions=None):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr, permissions=permissions or {})


def _login(client, *, admin_id, is_super):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = ["users.view"]


def _parent_of(child_id):
    row = db().execute("SELECT parent_admin_id FROM admins WHERE id=?", (child_id,)).fetchone()
    return row["parent_admin_id"] if row else None


def _by_username(u):
    from app.radius.db.repos import admins_repo
    return admins_repo.get_by_username(u)


# ═══ migration + flag ═══════════════════════════════════════════════════════
def test_migration_added_parent_column(app):
    with app.app_context():
        cols = [r[1] for r in db().execute("PRAGMA table_info(admins)").fetchall()]
    assert "parent_admin_id" in cols


def test_sub_manager_default_flag_off(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def")
        assert mg.can_create_sub_managers(m, tenant_id=1) is False


# ═══ creation gated by the flag ═════════════════════════════════════════════
def test_create_sub_manager_blocked_without_flag(app):
    with app.app_context():
        m = _mgr("m_noflag")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/business-operators/sub-managers",
                      data={"_csrf_token": "off-csrf", "username": "child1",
                            "password": "x12345678"}).status_code == 403
    with app.app_context():
        assert _by_username("child1") is None


def test_create_sub_manager_with_flag_sets_parent(app):
    with app.app_context():
        m = _mgr("m_flag"); _policy(m, permissions={"can_create_sub_managers": True})
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/business-operators/sub-managers",
                   data={"_csrf_token": "off-csrf", "username": "child2",
                         "password": "x12345678"})
        assert r.status_code in (302, 303)
    with app.app_context():
        child = _by_username("child2")
        assert child is not None
        assert _parent_of(int(child.id)) == m       # parent link set


# ═══ delegation ceiling (⊆ parent) ══════════════════════════════════════════
def test_delegation_clamped_to_parent(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        parent = _mgr("p1")
        # parent HAS create_subscriber but NOT give_loan
        _policy(parent, permissions={"can_create_sub_managers": True,
                                     "can_create_subscriber": True,
                                     "can_give_loan": False})
        child = _mgr("c1")
        db().execute("UPDATE admins SET parent_admin_id=? WHERE id=?", (parent, child))
    with app.test_client() as c:
        _login(c, admin_id=parent, is_super=False)
        # parent tries to delegate BOTH create (has) and loan (does NOT have)
        r = c.post(f"/admin/radius/business-operators/sub-managers/{child}/delegate",
                   data={"_csrf_token": "off-csrf",
                         "flag_can_create_subscriber": "1",
                         "flag_can_give_loan": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        # granted the one the parent has …
        assert mg.action_permitted(child, "subscriber.create", tenant_id=1) is True
        # … but the escalation (loan, parent lacks) was DROPPED
        assert mg.action_permitted(child, "subscriber.loan", tenant_id=1) is False


def test_delegation_action_clamped(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        parent = _mgr("p2")
        _policy(parent, permissions={"can_create_sub_managers": True})
        # parent granted store.deposit_approve, but NOT store.withdraw_approve
        mg.set_action_override(parent, "store.deposit_approve", True, tenant_id=1)
        child = _mgr("c2")
        db().execute("UPDATE admins SET parent_admin_id=? WHERE id=?", (parent, child))
    with app.test_client() as c:
        _login(c, admin_id=parent, is_super=False)
        c.post(f"/admin/radius/business-operators/sub-managers/{child}/delegate",
               data={"_csrf_token": "off-csrf",
                     "action_store.deposit_approve": "1",
                     "action_store.withdraw_approve": "1"})
    with app.app_context():
        assert mg.action_permitted(child, "store.deposit_approve", tenant_id=1) is True   # parent has it
        assert mg.action_permitted(child, "store.withdraw_approve", tenant_id=1) is False  # parent lacks → dropped


def test_non_parent_cannot_delegate(app):
    with app.app_context():
        stranger = _mgr("stranger"); _policy(stranger, permissions={"can_create_sub_managers": True})
        other_parent = _mgr("op"); child = _mgr("c3")
        db().execute("UPDATE admins SET parent_admin_id=? WHERE id=?", (other_parent, child))
    with app.test_client() as c:
        _login(c, admin_id=stranger, is_super=False)
        # stranger is not child's parent → 403
        assert c.post(f"/admin/radius/business-operators/sub-managers/{child}/delegate",
                      data={"_csrf_token": "off-csrf", "flag_can_create_subscriber": "1"}
                      ).status_code == 403


def test_clamp_helper_unit(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        parent = _mgr("pu")
        _policy(parent, permissions={"can_create_subscriber": True})
        flags, actions = mg.clamp_delegation(
            parent, flags={"can_create_subscriber": True, "can_give_loan": True},
            tenant_id=1)
        assert flags["can_create_subscriber"] is True
        assert flags["can_give_loan"] is False   # parent lacks → clamped off
