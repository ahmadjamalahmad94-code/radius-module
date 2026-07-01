"""Stage D — dangerous-action gates.

  • bulk.ops (default OFF) → an ADDITIONAL gate over each *_bulk endpoint's own
    action: a manager cannot run bulk edit/delete unless the owner grants it,
    even if the single-row action is allowed.
  • subscriber «reassign» field (manager_id) → the owner can lock a manager
    from moving a subscriber to another responsible manager (field-control).

Note: data reset/wipe stays super-only (blueprint __super__); there is no
manager hard-delete path (delete = archive → recycle bin). Owner/super bypass.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_dangerous.db")
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


def _grant_action(mgr, key, val=True):
    from app.radius.services import manager_grants as mg
    mg.set_action_override(mgr, key, val, tenant_id=1)


def _login(client, *, admin_id, is_super,
           perms=("users.view", "users.delete", "users.change_status")):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


# ═══ registry ═══════════════════════════════════════════════════════════════
def test_bulk_and_reassign_registered(app):
    from app.radius.services import manager_grants as mg
    assert "bulk.ops" in mg.ACTION_REGISTRY
    assert "reassign" in mg.field_keys("subscriber")
    with app.app_context():
        m = _mgr("m_reg")
        assert mg.action_permitted(m, "bulk.ops", tenant_id=1) is False   # default OFF
        grp = next((g for g in mg.action_catalog(m, tenant_id=1)
                    if any(a["key"] == "bulk.ops" for a in g["actions"])), None)
        assert grp is not None   # virtual action IS shown in the matrix


# ═══ bulk.ops additional gate ═══════════════════════════════════════════════
def test_bulk_delete_blocked_without_bulk_ops(app):
    # single-row delete (subscriber.delete) defaults allowed, but bulk needs bulk.ops
    with app.app_context():
        m = _mgr("m_bulk")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/users/bulk-delete",
                   data={"_csrf_token": "off-csrf", "usernames": "a,b"})
        assert r.status_code == 403


def test_bulk_delete_allowed_with_bulk_ops(app):
    with app.app_context():
        m = _mgr("m_bulk2"); _grant_action(m, "bulk.ops")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/users/bulk-delete",
                   data={"_csrf_token": "off-csrf", "usernames": "a,b"})
        assert r.status_code != 403


def test_single_delete_still_works_without_bulk_ops(app):
    # bulk.ops must NOT gate the single-row path
    with app.app_context():
        m = _mgr("m_single")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/users/nobody/delete",
                   data={"_csrf_token": "off-csrf"})
        assert r.status_code != 403     # subscriber.delete default allowed


def test_super_bypasses_bulk_gate(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        r = c.post("/admin/radius/users/bulk-delete",
                   data={"_csrf_token": "off-csrf", "usernames": "a,b"})
        assert r.status_code != 403


# ═══ reassign field lock ════════════════════════════════════════════════════
def test_reassign_field_lock(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_re")
        mg.set_field_grants(m, "subscriber", ["name"], tenant_id=1)   # reassign NOT granted
        assert "manager_id" in mg.reverted_attrs(m, "subscriber", tenant_id=1)
        mg.set_field_grants(m, "subscriber", ["name", "reassign"], tenant_id=1)
    with app.app_context():
        assert "manager_id" not in mg.reverted_attrs(m, "subscriber", tenant_id=1)


# ═══ config persists ════════════════════════════════════════════════════════
def test_policy_persists_bulk_ops(app):
    with app.app_context():
        m = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf", "action_bulk.ops": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.action_permitted(m, "bulk.ops", tenant_id=1) is True
