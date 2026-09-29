"""Sessions / connected-view granular action permissions («وسّع المجال»).

Every action on the connected-sessions view is its own independently-grantable,
server-enforced permission (moved out of the coarse subscriber grouping into a
dedicated «الجلسات» section):

  • session.edit         → live edit IP/speed via CoA (online_coa_set_ip/speed)
  • session.lock_mac     → pin MAC from the live session (online_lock_mac)
  • session.lock_ip      → pin IP from the live session (online_lock_ip)
  • session.disconnect   → disconnect an active session (online_disconnect)
  • session.force_close  → force-close a session (online_force_close)
  • session.reconcile    → reconcile/sync sessions (online_reconcile)
  • session.temp_speed   → temporary speed (online_temp_speed[_cancel])

fix wave 2 (p01/D15): each session action is DERIVED from its RBAC key — the
role's permission matrix is the single control (no separate hidden gate that
silently 403s a manager who holds «online.disconnect»):
  disconnect/force_close/reconcile <- online.disconnect · lock_mac <- online.lock_mac
  lock_ip <- online.lock_ip · edit <- online.lock_ip | users.temp_speed
  temp_speed <- users.temp_speed
The owner can still switch one action OFF for one manager (explicit override). NOTE: session.lock_mac/lock_ip reuse the subscriber lock endpoints but get
their OWN session-scoped permission key (the owner controls them from the
sessions view independently). Block-test each; owner/super bypasses.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_sessions.db")
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
                                 full_name="Owner", is_super_admin=True)  # min-id owner
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _mgr(username="m1", perms=()) -> int:
    """A plain manager with his OWN limited role (``perms``) — not the default
    «مدير عام» role, which now carries every non-owner permission."""
    from app.radius.db.repos import admins_repo

    role = admins_repo.create_role(name="r_" + username, permissions=tuple(perms))
    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", role_id=role.id, is_super_admin=False)
    return int(adm.id)


# RBAC perms so the pre-existing route guards pass → the derived action gate
# follows these keys.
_PERMS = ["online.view", "online.disconnect", "online.lock_mac", "online.lock_ip",
          "users.temp_speed"]


def _login(client, *, admin_id, is_super, perms=_PERMS):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


def _off(mgr, key):
    """The owner switches ONE derived action off for this manager."""
    from app.radius.services import manager_grants as mg
    mg.set_action_override(mgr, key, False, tenant_id=1)


def _without(*keys):
    return [p for p in _PERMS if p not in keys]


# ═══ registry + section ═════════════════════════════════════════════════════
def test_sessions_section_and_actions_registered(app):
    from app.radius.services import manager_grants as mg
    assert "sessions" in mg.MANAGER_SECTION_REGISTRY
    for k in ("session.edit", "session.lock_mac", "session.lock_ip",
              "session.disconnect", "session.force_close", "session.reconcile",
              "session.temp_speed"):
        assert k in mg.ACTION_REGISTRY
    assert mg.endpoint_action("online_disconnect") == "session.disconnect"
    assert mg.endpoint_action("online_force_close") == "session.force_close"
    assert mg.endpoint_action("online_lock_mac") == "session.lock_mac"
    assert mg.endpoint_action("online_lock_ip") == "session.lock_ip"
    assert mg.endpoint_action("online_coa_set_ip") == "session.edit"
    assert mg.section_of_endpoint("online_disconnect") == "sessions"


def test_session_actions_off_without_their_rbac_key(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def", perms=("online.view",))
        for k in ("session.disconnect", "session.force_close", "session.lock_mac",
                  "session.lock_ip", "session.edit", "session.temp_speed"):
            assert mg.action_permitted(m, k, tenant_id=1) is False


def test_session_actions_derive_from_rbac_keys(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_der", perms=_PERMS)
        for k in ("session.disconnect", "session.force_close", "session.reconcile",
                  "session.lock_mac", "session.lock_ip", "session.edit",
                  "session.temp_speed"):
            assert mg.is_derived_action(k), k
            assert mg.action_permitted(m, k, tenant_id=1) is True, k


def test_derived_session_actions_not_in_editable_catalog(app):
    """D15: no separate checkbox — the role's RBAC matrix is the control."""
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_cat")
        keys = {a["key"] for g in mg.action_catalog(m, tenant_id=1) for a in g["actions"]}
        assert not keys & {"session.disconnect", "session.force_close", "session.lock_mac",
                           "session.lock_ip", "session.edit", "session.temp_speed"}


# ═══ block-tests per action ═════════════════════════════════════════════════
def test_disconnect_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_disc", perms=_without("online.disconnect"))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=_without("online.disconnect"))
        assert c.post("/admin/radius/online/disconnect",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code == 403


def test_disconnect_allowed_with_key(app):
    """p01/D15: «online.disconnect» alone is enough (no hidden gate)."""
    with app.app_context():
        m = _mgr("m_disc2", perms=_PERMS)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/online/disconnect",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code != 403


def test_disconnect_owner_can_switch_off_per_manager(app):
    with app.app_context():
        m = _mgr("m_disc3", perms=_PERMS); _off(m, "session.disconnect")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/online/disconnect",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code == 403


def test_force_close_can_be_switched_off_separately(app):
    with app.app_context():
        m = _mgr("m_fc", perms=_PERMS); _off(m, "session.force_close")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        # disconnect still allowed; force-close switched off on its own → blocked
        assert c.post("/admin/radius/online/disconnect",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code != 403
        assert c.post("/admin/radius/online/force-close",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code == 403


def test_force_close_allowed_with_key(app):
    with app.app_context():
        m = _mgr("m_fc2", perms=_PERMS)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/online/force-close",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code != 403


def test_lock_mac_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_lm", perms=_without("online.lock_mac"))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=_without("online.lock_mac"))
        assert c.post("/admin/radius/online/lock-mac",
                      data={"_csrf_token": "off-csrf", "username": "x",
                            "session_id": "s"}).status_code == 403


def test_lock_ip_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_li", perms=_without("online.lock_ip"))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=_without("online.lock_ip"))
        assert c.post("/admin/radius/online/lock-ip",
                      data={"_csrf_token": "off-csrf", "username": "x",
                            "session_id": "s"}).status_code == 403


def test_session_edit_coa_blocked_without_keys(app):
    perms = _without("online.lock_ip", "users.temp_speed")
    with app.app_context():
        m = _mgr("m_ed", perms=perms)
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=perms)
        assert c.post("/admin/radius/online/coa/set-ip",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code == 403


def test_session_edit_coa_switched_off_per_manager(app):
    with app.app_context():
        m = _mgr("m_ed2", perms=_PERMS); _off(m, "session.edit")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/online/coa/set-ip",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code == 403


def test_temp_speed_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_ts", perms=_without("users.temp_speed"))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=_without("users.temp_speed"))
        assert c.post("/admin/radius/online/temp-speed",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code == 403


# ═══ owner/super bypass ═════════════════════════════════════════════════════
def test_super_bypasses_session_actions(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        assert c.post("/admin/radius/online/disconnect",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code != 403
        assert c.post("/admin/radius/online/force-close",
                      data={"_csrf_token": "off-csrf", "username": "x"}
                      ).status_code != 403


# ═══ config route: derived actions follow the role, not a checkbox ══════════
def test_policy_route_does_not_store_derived_session_actions(app):
    with app.app_context():
        m = _mgr("m_cfg", perms=("online.view", "online.disconnect"))
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf",
                         "action_session.disconnect": "1",
                         "action_session.lock_mac": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.action_permitted(m, "session.disconnect", tenant_id=1) is True
        # a posted checkbox cannot grant what the role lacks
        assert mg.action_permitted(m, "session.lock_mac", tenant_id=1) is False
        flat = (mg._grants_row(m, 1).get("action_grants") or {}).get("_actions") or {}
        assert "session.disconnect" not in flat and "session.lock_mac" not in flat
