"""Stage E — communications (cost control), each channel its own permission.

  • comms.sms       → send SMS (users_send_sms[_bulk], communications_send)
  • comms.whatsapp  → send/test WhatsApp (whatsapp_settings/test/cloud_test)
  • comms.templates → edit notification templates (communications_templates)

fix wave 2 (p01/D15): each channel DERIVES from its RBAC key (sms/templates <-
users.send_message, whatsapp <- settings.edit) — the role matrix is the cost
control; the owner can still switch one channel off for one manager.
Owner/super bypass. Block-test each.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_comms.db")
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


_BASE = ("users.send_message", "users.view")


def _mgr(username="m1", perms=_BASE) -> int:
    """A plain manager with his OWN role — not the default «مدير عام» role,
    which now carries every non-owner permission."""
    from app.radius.db.repos import admins_repo

    role = admins_repo.create_role(name="r_" + username, permissions=tuple(perms))
    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", role_id=role.id, is_super_admin=False)
    return int(adm.id)


def _off(mgr, key):
    """The owner switches ONE derived channel off for this manager."""
    from app.radius.services import manager_grants as mg
    mg.set_action_override(mgr, key, False, tenant_id=1)


def _login(client, *, admin_id, is_super, perms=_BASE):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


# ═══ registry ═══════════════════════════════════════════════════════════════
def test_comms_registered(app):
    from app.radius.services import manager_grants as mg
    assert "communications" in mg.MANAGER_SECTION_REGISTRY
    for k in ("comms.sms", "comms.whatsapp", "comms.templates"):
        assert k in mg.ACTION_REGISTRY
    assert mg.endpoint_action("users_send_sms") == "comms.sms"
    assert mg.endpoint_action("whatsapp_test") == "comms.whatsapp"
    assert mg.endpoint_action("communications_templates") == "comms.templates"


def test_comms_off_without_their_rbac_keys(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def", perms=("users.view",))
        for k in ("comms.sms", "comms.whatsapp", "comms.templates"):
            assert mg.action_permitted(m, k, tenant_id=1) is False


def test_comms_derive_from_rbac_keys(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_der")
        assert mg.action_permitted(m, "comms.sms", tenant_id=1) is True
        assert mg.action_permitted(m, "comms.templates", tenant_id=1) is True
        # WhatsApp is a SETTINGS operation (API credentials) → settings.edit
        assert mg.action_permitted(m, "comms.whatsapp", tenant_id=1) is False


# ═══ SMS ════════════════════════════════════════════════════════════════════
def test_sms_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_sms", perms=("users.view",))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=("users.view",))
        assert c.post("/admin/radius/users/x/sms",
                      data={"_csrf_token": "off-csrf", "message": "hi"}
                      ).status_code == 403


def test_sms_switched_off_per_manager(app):
    with app.app_context():
        m = _mgr("m_sms1"); _off(m, "comms.sms")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/users/x/sms",
                      data={"_csrf_token": "off-csrf", "message": "hi"}
                      ).status_code == 403


def test_sms_allowed_with_key(app):
    with app.app_context():
        m = _mgr("m_sms2")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/users/x/sms",
                      data={"_csrf_token": "off-csrf", "message": "hi"}
                      ).status_code != 403


def test_sms_key_does_not_grant_whatsapp(app):
    with app.app_context():
        m = _mgr("m_sms3")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/whatsapp/test",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


# ═══ WhatsApp ═══════════════════════════════════════════════════════════════
def test_whatsapp_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_wa")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/whatsapp/test",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_whatsapp_allowed_with_key(app):
    perms = _BASE + ("settings.edit",)
    with app.app_context():
        m = _mgr("m_wa2", perms=perms)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=perms)
        assert c.post("/admin/radius/whatsapp/test",
                      data={"_csrf_token": "off-csrf"}).status_code != 403


# ═══ templates ══════════════════════════════════════════════════════════════
def test_templates_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_tpl", perms=("users.view",))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=("users.view",))
        assert c.post("/admin/radius/communications/templates",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_templates_switched_off_per_manager(app):
    with app.app_context():
        m = _mgr("m_tpl1"); _off(m, "comms.templates")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/communications/templates",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_templates_allowed_with_key(app):
    with app.app_context():
        m = _mgr("m_tpl2")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/communications/templates",
                      data={"_csrf_token": "off-csrf"}).status_code != 403


# ═══ super + config ═════════════════════════════════════════════════════════
def test_super_bypasses_comms(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        assert c.post("/admin/radius/whatsapp/test",
                      data={"_csrf_token": "off-csrf"}).status_code != 403


def test_policy_does_not_store_derived_comms(app):
    with app.app_context():
        m = _mgr("m_cfg", perms=("users.view",))
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf", "action_comms.sms": "1",
                         "action_comms.templates": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        # a posted checkbox cannot grant what the role lacks
        assert mg.action_permitted(m, "comms.sms", tenant_id=1) is False
        assert mg.action_permitted(m, "comms.templates", tenant_id=1) is False
        assert mg.action_permitted(m, "comms.whatsapp", tenant_id=1) is False
