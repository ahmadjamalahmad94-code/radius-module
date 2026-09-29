"""Store / e-commerce granular action permissions («وسّع المجال»).

Splits the coarse store.review into independently-grantable, server-enforced
per-manager actions, and adds store-user (card-user) action gates:

  • store.deposit_approve   → approve/reject store DEPOSIT requests
  • store.withdraw_approve  → approve/reject store WITHDRAWAL requests
  • storeuser.create        → create a store (card) user
  • storeuser.edit          → modify a store user (recharge / purchase)
  • storeuser.password      → change a store user's password

fix wave 2 (p01/D15): each action DERIVES from its RBAC key (deposit/withdraw
<- store.review, storeuser.create <- store.user_add, storeuser.edit <-
store.user_recharge|store.user_purchase, storeuser.password <- store.user_edit,
storeuser.delete <- store.user_delete). The owner can still switch ONE action
off for one manager (explicit override) — e.g. deposits yes, withdrawals no.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_store.db")
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


def _mgr(username="m1", perms=None) -> int:
    """A plain manager with his OWN role (``perms``, default ``_PERMS``) — not the
    default «مدير عام» role, which now carries every non-owner permission."""
    from app.radius.db.repos import admins_repo

    role = admins_repo.create_role(name="r_" + username,
                                   permissions=tuple(_PERMS if perms is None else perms))
    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", role_id=role.id, is_super_admin=False)
    return int(adm.id)


# store.review clears the store_support RBAC guard; the store.* keys clear the
# card-user RBAC guard. p01/D15: the store actions DERIVE from these keys.
_PERMS = ["store.review", "cards.recharge", "cards.view",
          "store.view", "store.package_add", "store.user_add", "store.user_edit",
          "store.user_recharge", "store.user_purchase", "store.user_delete"]


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


# ═══ registry + mapping ═════════════════════════════════════════════════════
def test_registry_has_store_actions(app):
    from app.radius.services import manager_grants as mg
    for k in ("store.deposit_approve", "store.withdraw_approve",
              "storeuser.create", "storeuser.edit", "storeuser.password"):
        assert k in mg.ACTION_REGISTRY
    assert mg.endpoint_action("store_support_deposit_confirm") == "store.deposit_approve"
    assert mg.endpoint_action("store_support_withdrawal_confirm") == "store.withdraw_approve"
    assert mg.endpoint_action("card_users_create") == "storeuser.create"
    assert mg.endpoint_action("card_user_password") == "storeuser.password"
    assert mg.endpoint_action("card_user_recharge") == "storeuser.edit"


def test_store_actions_off_without_their_rbac_keys(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def", perms=("store.view", "cards.view"))
        for k in ("store.deposit_approve", "store.withdraw_approve",
                  "storeuser.create", "storeuser.edit", "storeuser.password"):
            assert mg.action_permitted(m, k, tenant_id=1) is False


def test_store_actions_derive_from_rbac_keys(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_der")
        for k in ("store.deposit_approve", "store.withdraw_approve",
                  "storeuser.create", "storeuser.edit", "storeuser.password",
                  "storeuser.delete"):
            assert mg.is_derived_action(k), k
            assert mg.action_permitted(m, k, tenant_id=1) is True, k


def test_derived_store_actions_not_in_editable_catalog(app):
    """D15: no separate checkbox — the role's RBAC matrix is the control."""
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_cat")
        keys = {a["key"] for g in mg.action_catalog(m, tenant_id=1) for a in g["actions"]}
        assert not keys & {"store.deposit_approve", "store.withdraw_approve",
                           "storeuser.create", "storeuser.password"}


# ═══ deposit / withdraw ═════════════════════════════════════════════════════
def test_deposit_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_dep", perms=_without("store.review"))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=_without("store.review"))
        r = c.post("/admin/radius/store-support/deposits/1/confirm",
                   data={"_csrf_token": "off-csrf"})
        assert r.status_code == 403


def test_deposit_allowed_with_key(app):
    with app.app_context():
        m = _mgr("m_dep2")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/store-support/deposits/1/confirm",
                   data={"_csrf_token": "off-csrf"})
        assert r.status_code != 403      # gate passes (handler 302s on missing req)


def test_withdrawal_can_be_switched_off_separately(app):
    """The owner can still keep deposits but switch withdrawals off for one
    manager (explicit per-manager override)."""
    with app.app_context():
        m = _mgr("m_dep_only"); _off(m, "store.withdraw_approve")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/store-support/deposits/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code != 403
        assert c.post("/admin/radius/store-support/withdrawals/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_deposit_can_be_switched_off_separately(app):
    with app.app_context():
        m = _mgr("m_wd"); _off(m, "store.deposit_approve")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/store-support/withdrawals/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code != 403
        assert c.post("/admin/radius/store-support/deposits/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_deposit_reject_also_gated(app):
    with app.app_context():
        m = _mgr("m_rej"); _off(m, "store.deposit_approve")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/store-support/deposits/1/reject",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


# ═══ store-user actions ═════════════════════════════════════════════════════
def test_storeuser_create_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_su", perms=_without("store.user_add"))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=_without("store.user_add"))
        r = c.post("/admin/radius/card-users",
                   data={"_csrf_token": "off-csrf", "display_name": "x y z",
                         "mobile": "0790000000", "password": "pass1234"})
        assert r.status_code == 403


def test_storeuser_create_allowed_with_key(app):
    with app.app_context():
        m = _mgr("m_su2")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/card-users",
                   data={"_csrf_token": "off-csrf", "display_name": "x y z",
                         "mobile": "0790000000", "password": "pass1234"})
        assert r.status_code != 403


def test_storeuser_password_blocked_without_key(app):
    with app.app_context():
        m = _mgr("m_pw", perms=_without("store.user_edit"))
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=_without("store.user_edit"))
        assert c.post("/admin/radius/card-users/1/password",
                      data={"_csrf_token": "off-csrf", "password": "pass1234"}
                      ).status_code == 403


def test_storeuser_edit_recharge_switched_off_per_manager(app):
    with app.app_context():
        m = _mgr("m_ed"); _off(m, "storeuser.edit")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/card-users/1/recharge",
                      data={"_csrf_token": "off-csrf", "amount": "5"}
                      ).status_code == 403


# ═══ owner/super bypass ═════════════════════════════════════════════════════
def test_super_bypasses_store_actions(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        assert c.post("/admin/radius/store-support/deposits/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code != 403
        assert c.post("/admin/radius/card-users",
                      data={"_csrf_token": "off-csrf", "display_name": "a b c",
                            "mobile": "0791111111", "password": "pass1234"}
                      ).status_code != 403


# ═══ config route: derived actions follow the role, not a checkbox ══════════
def test_policy_route_does_not_store_derived_store_actions(app):
    with app.app_context():
        m = _mgr("m_cfg", perms=("store.view", "store.review"))
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf",
                         "action_store.deposit_approve": "1",
                         "action_storeuser.create": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.action_permitted(m, "store.deposit_approve", tenant_id=1) is True
        assert mg.action_permitted(m, "store.withdraw_approve", tenant_id=1) is True
        # a posted checkbox cannot grant what the role lacks (store.user_add)
        assert mg.action_permitted(m, "storeuser.create", tenant_id=1) is False
        flat = (mg._grants_row(m, 1).get("action_grants") or {}).get("_actions") or {}
        assert "store.deposit_approve" not in flat and "storeuser.create" not in flat
