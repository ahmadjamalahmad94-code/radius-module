"""Stage C — sensitive-data visibility (server-enforced).

  • can_see_password (default OFF) → the subscriber password is stripped
    server-side from the edit form (not in the DOM). Saving with an empty
    password preserves the stored one, so masking never wipes it.
  • data.export (default OFF, gate_get) → CSV/Excel/PDF export endpoints
    return 403 unless the owner grants the manager export.

Owner/super always sees / exports. Block-test each.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_datavis.db")
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


def _sub(username, *, password, manager_id):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password=password,
        status="enabled", manager_id=manager_id))


def _grant(mgr, flag, val=True):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr, permissions={flag: val})


def _grant_action(mgr, key, val=True):
    from app.radius.services import manager_grants as mg
    mg.set_action_override(mgr, key, val, tenant_id=1)


def _login(client, *, admin_id, is_super, perms=("users.view", "cards.view")):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


# ═══ can_see_password projection ════════════════════════════════════════════
def test_password_default_hidden(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def")
        assert mg.can_see(m, "can_see_password", tenant_id=1) is False


def test_password_masked_from_manager(app):
    with app.app_context():
        m = _mgr("m_hide"); _sub("secretsub", password="secretpw99", manager_id=m)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        html = c.get("/admin/radius/users/secretsub/edit").get_data(as_text=True)
    assert "secretpw99" not in html      # stripped server-side


def test_password_visible_with_grant(app):
    with app.app_context():
        m = _mgr("m_show"); _sub("shownsub", password="secretpw99", manager_id=m)
        _grant(m, "can_see_password", True)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        html = c.get("/admin/radius/users/shownsub/edit").get_data(as_text=True)
    assert "secretpw99" in html


def test_password_visible_to_super(app):
    with app.app_context():
        m = _mgr("m_sup"); _sub("supsub", password="secretpw99", manager_id=m)
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        html = c.get("/admin/radius/users/supsub/edit").get_data(as_text=True)
    assert "secretpw99" in html


# ═══ data.export gate (GET endpoints) ═══════════════════════════════════════
def test_export_blocked_without_grant(app):
    with app.app_context():
        m = _mgr("m_exp")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.get("/admin/radius/cards/batches/export.csv").status_code == 403


def test_export_allowed_with_grant(app):
    # التوحيد: التصدير صار مُشتقًّا من صلاحية users.export (لا منحة فعلٍ منفصلة).
    with app.app_context():
        m = _mgr("m_exp2")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False,
               perms=("users.view", "cards.view", "users.export"))
        assert c.get("/admin/radius/cards/batches/export.csv").status_code != 403


def test_export_allowed_for_super(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        assert c.get("/admin/radius/cards/batches/export.csv").status_code != 403


# ═══ config persists ════════════════════════════════════════════════════════
def test_policy_persists_datavis(app):
    with app.app_context():
        m = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf", "can_see_password": "1",
                         "action_data.export": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.can_see(m, "can_see_password", tenant_id=1) is True
        assert mg.action_permitted(m, "data.export", tenant_id=1) is True
