"""Stage F (F1) — time-limited permissions.

The owner sets an expiry date on a manager's grants. After it passes, the
manager's GRANTS (can_* flags + action gates) are automatically revoked — the
manager falls back to the restrictive baseline. Restrictions (numeric caps,
hidden sections, field-control) persist. Empty date = permanent. Super bypass.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_timelimited.db")
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


def _policy(mgr, *, permissions=None, limits=None):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr,
        permissions=permissions or {}, limits=limits or {})


def _login(client, *, admin_id, is_super, perms=("users.view", "users.create")):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


# ═══ unit ═══════════════════════════════════════════════════════════════════
def test_no_expiry_grants_active(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_perm")
        _policy(m, permissions={"can_create_subscriber": True})
        assert mg.grants_expired(m, tenant_id=1) is False
        assert mg.action_permitted(m, "subscriber.create", tenant_id=1) is True


def test_future_expiry_grants_active(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_future")
        _policy(m, permissions={"can_create_subscriber": True},
                limits={"grants_expire_at": "2999-12-31"})
        assert mg.grants_expired(m, tenant_id=1) is False
        assert mg.action_permitted(m, "subscriber.create", tenant_id=1) is True


def test_past_expiry_revokes_grants(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_past")
        # flag grant + an rbac action override, both should be revoked after expiry
        _policy(m, permissions={"can_create_subscriber": True},
                limits={"grants_expire_at": "2000-01-01"})
        mg.set_action_override(m, "store.deposit_approve", True, tenant_id=1)
    with app.app_context():
        assert mg.grants_expired(m, tenant_id=1) is True
        assert mg.action_permitted(m, "subscriber.create", tenant_id=1) is False   # flag revoked
        assert mg.action_permitted(m, "store.deposit_approve", tenant_id=1) is False  # override revoked


def test_expiry_keeps_restrictions(app):
    # numeric caps (a restriction) must PERSIST past expiry
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_caps")
        _policy(m, limits={"max_subscribers": 3, "grants_expire_at": "2000-01-01"})
        assert mg.limit_value(m, "max_subscribers", tenant_id=1) == 3


# ═══ route enforcement ══════════════════════════════════════════════════════
def test_expired_manager_blocked_at_create(app):
    with app.app_context():
        m = _mgr("m_route")
        _policy(m, permissions={"can_create_subscriber": True},
                limits={"grants_expire_at": "2000-01-01"})
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        # can_create_subscriber was granted but has EXPIRED → 403
        r = c.post("/admin/radius/users",
                   data={"_csrf_token": "off-csrf", "username": "n", "password": "p1234567"})
        assert r.status_code == 403


def test_super_unaffected_by_expiry(app):
    with app.app_context():
        _policy(1, permissions={"can_create_subscriber": True},
                limits={"grants_expire_at": "2000-01-01"})
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        r = c.post("/admin/radius/users",
                   data={"_csrf_token": "off-csrf", "username": "sup", "password": "p1234567"})
        assert r.status_code in (302, 303)


# ═══ config persists ════════════════════════════════════════════════════════
def test_policy_persists_expiry(app):
    with app.app_context():
        m = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf", "grants_expire_at": "2000-06-01",
                         "can_create_subscriber": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.grants_expired(m, tenant_id=1) is True
        # the granted flag is revoked by the past expiry
        assert mg.action_permitted(m, "subscriber.create", tenant_id=1) is False
