"""F2 — permission-template presets.

The owner saves a named bundle of a manager's granular grants and applies it to
another manager in one click. Applying overwrites the target's grants with the
preset's. Owner-only (admins.policy). Block-tests + effective-perms match.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_presets.db")
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


def _policy(mgr, *, permissions=None, limits=None):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr,
        permissions=permissions or {}, limits=limits or {})


def _login(client, *, admin_id, is_super, perms=("admins.policy", "admins.view")):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


def test_migration_created_presets_table(app):
    with app.app_context():
        cols = [r[1] for r in db().execute(
            "PRAGMA table_info(manager_permission_presets)").fetchall()]
    for c in ("name", "permissions_json", "limits_json", "section_access_json",
              "action_grants_json", "field_grants_json"):
        assert c in cols


def test_create_preset_from_manager_and_apply(app):
    from app.radius.services import manager_presets as mp
    from app.radius.services import manager_grants as mg
    with app.app_context():
        src = _mgr("src"); dst = _mgr("dst")
        # source manager: a flag grant + a numeric cap + an action override
        _policy(src, permissions={"can_create_subscriber": True},
                limits={"max_subscribers": 9})
        mg.set_action_override(src, "store.deposit_approve", True, tenant_id=1)
        preset = mp.create_preset("gold", tenant_id=1, source_manager_id=src)
    with app.app_context():
        # destination starts empty
        assert mg.action_permitted(dst, "subscriber.create", tenant_id=1) is False
        mp.apply_preset(int(preset["id"]), dst, tenant_id=1)
    with app.app_context():
        # destination now matches the preset (= source's grants)
        assert mg.action_permitted(dst, "subscriber.create", tenant_id=1) is True
        assert mg.action_permitted(dst, "store.deposit_approve", tenant_id=1) is True
        assert mg.limit_value(dst, "max_subscribers", tenant_id=1) == 9


def test_duplicate_name_rejected(app):
    from app.radius.services import manager_presets as mp
    with app.app_context():
        mp.create_preset("dup", tenant_id=1)
        with pytest.raises(mp.ManagerPresetError):
            mp.create_preset("dup", tenant_id=1)


def test_delete_preset(app):
    from app.radius.services import manager_presets as mp
    with app.app_context():
        p = mp.create_preset("temp", tenant_id=1)
        mp.delete_preset(int(p["id"]), tenant_id=1)
        assert mp.list_presets(tenant_id=1) == []


# ═══ routes ═════════════════════════════════════════════════════════════════
def test_route_create_and_apply_preset(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        src = _mgr("rsrc"); dst = _mgr("rdst")
        _policy(src, permissions={"can_create_subscriber": True})
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        # save from src
        c.post("/admin/radius/business-operators/presets",
               data={"_csrf_token": "off-csrf", "name": "team", "source_manager_id": str(src)})
        with app.app_context():
            from app.radius.services import manager_presets as mp
            pid = int(mp.list_presets(tenant_id=1)[0]["id"])
        # apply to dst
        r = c.post(f"/admin/radius/business-operators/manager/{dst}/apply-preset",
                   data={"_csrf_token": "off-csrf", "preset_id": str(pid)})
        assert r.status_code in (302, 303)
    with app.app_context():
        assert mg.action_permitted(dst, "subscriber.create", tenant_id=1) is True


def test_preset_routes_owner_only(app):
    with app.app_context():
        m = _mgr("plain")
    with app.test_client() as c:
        # a manager without admins.policy cannot manage presets
        _login(c, admin_id=m, is_super=False, perms=("users.view",))
        assert c.post("/admin/radius/business-operators/presets",
                      data={"_csrf_token": "off-csrf", "name": "x"}).status_code == 403
