"""Stage E — communications (cost control), each channel its own permission.

  • comms.sms       → send SMS (users_send_sms[_bulk], communications_send)
  • comms.whatsapp  → send/test WhatsApp (whatsapp_settings/test/cloud_test)
  • comms.templates → edit notification templates (communications_templates)

All default OFF (cost control): a manager cannot send SMS/WhatsApp nor edit
templates unless the owner grants it. Owner/super bypass. Block-test each.
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


def _mgr(username="m1") -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", is_super_admin=False)
    return int(adm.id)


def _grant(mgr, key, val=True):
    from app.radius.services import manager_grants as mg
    mg.set_action_override(mgr, key, val, tenant_id=1)


def _login(client, *, admin_id, is_super, perms=("users.send_message", "users.view")):
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


def test_comms_default_off(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def")
        for k in ("comms.sms", "comms.whatsapp", "comms.templates"):
            assert mg.action_permitted(m, k, tenant_id=1) is False


# ═══ SMS ════════════════════════════════════════════════════════════════════
def test_sms_blocked_without_grant(app):
    with app.app_context():
        m = _mgr("m_sms")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/users/x/sms",
                      data={"_csrf_token": "off-csrf", "message": "hi"}
                      ).status_code == 403


def test_sms_allowed_with_grant(app):
    with app.app_context():
        m = _mgr("m_sms2"); _grant(m, "comms.sms")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/users/x/sms",
                      data={"_csrf_token": "off-csrf", "message": "hi"}
                      ).status_code != 403


def test_sms_grant_does_not_grant_whatsapp(app):
    with app.app_context():
        m = _mgr("m_sms3"); _grant(m, "comms.sms")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/whatsapp/test",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


# ═══ WhatsApp ═══════════════════════════════════════════════════════════════
def test_whatsapp_blocked_without_grant(app):
    with app.app_context():
        m = _mgr("m_wa")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/whatsapp/test",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_whatsapp_allowed_with_grant(app):
    with app.app_context():
        m = _mgr("m_wa2"); _grant(m, "comms.whatsapp")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/whatsapp/test",
                      data={"_csrf_token": "off-csrf"}).status_code != 403


# ═══ templates ══════════════════════════════════════════════════════════════
def test_templates_blocked_without_grant(app):
    with app.app_context():
        m = _mgr("m_tpl")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/communications/templates",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_templates_allowed_with_grant(app):
    with app.app_context():
        m = _mgr("m_tpl2"); _grant(m, "comms.templates")
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


def test_policy_persists_comms(app):
    with app.app_context():
        m = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf", "action_comms.sms": "1",
                         "action_comms.templates": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.action_permitted(m, "comms.sms", tenant_id=1) is True
        assert mg.action_permitted(m, "comms.templates", tenant_id=1) is True
        assert mg.action_permitted(m, "comms.whatsapp", tenant_id=1) is False
