"""Store / e-commerce granular action permissions («وسّع المجال»).

Splits the coarse store.review into independently-grantable, server-enforced
per-manager actions, and adds store-user (card-user) action gates:

  • store.deposit_approve   → approve/reject store DEPOSIT requests
  • store.withdraw_approve  → approve/reject store WITHDRAWAL requests
  • storeuser.create        → create a store (card) user
  • storeuser.edit          → modify a store user (recharge / purchase)
  • storeuser.password      → change a store user's password

Default OFF (restrictive). The store.review RBAC guard stays on top (not
weakened). Each proven with a block-test: a restricted manager → 403 on that
specific action; owner/allowed → success. Owner can allow deposit but NOT
withdrawal, etc.
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


def _mgr(username="m1") -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", is_super_admin=False)
    return int(adm.id)


# store.review clears the store_support RBAC guard; cards.recharge clears the
# card-user RBAC guard — so the assertions exercise OUR per-action gate.
_PERMS = ["store.review", "cards.recharge", "cards.view"]


def _login(client, *, admin_id, is_super, perms=_PERMS):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


def _grant(mgr, key, val=True):
    from app.radius.services import manager_grants as mg
    mg.set_action_override(mgr, key, val, tenant_id=1)


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


def test_store_actions_default_off(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def")
        for k in ("store.deposit_approve", "store.withdraw_approve",
                  "storeuser.create", "storeuser.edit", "storeuser.password"):
            assert mg.action_permitted(m, k, tenant_id=1) is False


def test_store_appears_in_action_catalog(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_cat")
        cats = mg.action_catalog(m, tenant_id=1)
        store = next((g for g in cats if g["section"] == "store"), None)
        assert store is not None
        keys = {a["key"] for a in store["actions"]}
        assert {"store.deposit_approve", "store.withdraw_approve",
                "storeuser.create", "storeuser.password"} <= keys


# ═══ deposit / withdraw split (independently grantable) ═════════════════════
def test_deposit_blocked_without_grant(app):
    with app.app_context():
        m = _mgr("m_dep")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/store-support/deposits/1/confirm",
                   data={"_csrf_token": "off-csrf"})
        assert r.status_code == 403


def test_deposit_allowed_with_grant(app):
    with app.app_context():
        m = _mgr("m_dep2"); _grant(m, "store.deposit_approve")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/store-support/deposits/1/confirm",
                   data={"_csrf_token": "off-csrf"})
        assert r.status_code != 403      # gate passes (handler 302s on missing req)


def test_deposit_grant_does_not_grant_withdrawal(app):
    with app.app_context():
        m = _mgr("m_dep_only"); _grant(m, "store.deposit_approve")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/store-support/deposits/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code != 403
        # withdrawal is a SEPARATE permission → still blocked
        assert c.post("/admin/radius/store-support/withdrawals/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_withdrawal_allowed_only_with_its_grant(app):
    with app.app_context():
        m = _mgr("m_wd"); _grant(m, "store.withdraw_approve")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/store-support/withdrawals/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code != 403
        assert c.post("/admin/radius/store-support/deposits/1/confirm",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


def test_deposit_reject_also_gated(app):
    with app.app_context():
        m = _mgr("m_rej")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/store-support/deposits/1/reject",
                      data={"_csrf_token": "off-csrf"}).status_code == 403


# ═══ store-user actions ═════════════════════════════════════════════════════
def test_storeuser_create_blocked_without_grant(app):
    with app.app_context():
        m = _mgr("m_su")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/card-users",
                   data={"_csrf_token": "off-csrf", "display_name": "x y z",
                         "mobile": "0790000000", "password": "pass1234"})
        assert r.status_code == 403


def test_storeuser_create_allowed_with_grant(app):
    with app.app_context():
        m = _mgr("m_su2"); _grant(m, "storeuser.create")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/card-users",
                   data={"_csrf_token": "off-csrf", "display_name": "x y z",
                         "mobile": "0790000000", "password": "pass1234"})
        assert r.status_code != 403


def test_storeuser_password_blocked_without_grant(app):
    with app.app_context():
        m = _mgr("m_pw")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        assert c.post("/admin/radius/card-users/1/password",
                      data={"_csrf_token": "off-csrf", "password": "pass1234"}
                      ).status_code == 403


def test_storeuser_edit_recharge_blocked_without_grant(app):
    with app.app_context():
        m = _mgr("m_ed")
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


# ═══ config route persists the split ════════════════════════════════════════
def test_policy_route_persists_store_grants(app):
    with app.app_context():
        m = _mgr("m_cfg")
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
        assert mg.action_permitted(m, "storeuser.create", tenant_id=1) is True
        # withdrawal NOT granted in this POST → stays off
        assert mg.action_permitted(m, "store.withdraw_approve", tenant_id=1) is False
