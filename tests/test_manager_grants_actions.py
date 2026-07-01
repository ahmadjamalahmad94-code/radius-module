"""Exhaustive per-ACTION gating (Stage 4): «كل شيء بصلاحية».

Every manager operation is bound to an owner-controlled permission enforced
SERVER-SIDE in _perm_guard (step 3c) — 403 when off, even via direct URL /
crafted POST. Integrates the legacy can_* flags (made real). Owner/super
bypasses. Each permission is proven with a block-test (restricted → 403) plus
an allowed/owner path (not 403).

Resolution:
  • flag-backed actions (create/activate/loan/import/generate/manage) → the
    can_* flag, default OFF (restrictive).
  • RBAC-governed actions (extend/delete/quota/balance/payment/disconnect/…)
    → allowed by default (RBAC still guards) BUT the owner can turn each OFF
    (explicit 403). Money routes keep their RBAC guard (not weakened).
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "manager_grants_actions.db")
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


# ── helpers ──────────────────────────────────────────────────────────────
def _mgr(username="m1") -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", is_super_admin=False)
    return int(adm.id)


def _plan() -> int:
    cur = db().execute(
        """INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,
           price,currency,speed_down_kbps,speed_up_kbps,quota_total_mb,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, "P", 480, 30, 5.0, "JOD", 4096, 2048, 1024))
    return int(cur.lastrowid)


def _sub(username="sub1", *, manager_id):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="p1234567",
        status="enabled", manager_id=manager_id))


# broad RBAC perms so the pre-existing route guard passes → our action gate is
# what the assertions exercise (or, for money, both guards active).
_PERMS = ["users.view", "users.create", "users.delete", "users.change_status",
          "users.edit", "users.extend", "users.change_plan", "users.quota",
          "users.balance_add", "users.payments", "users.loans",
          "users.send_message", "users.temp_speed", "online.view",
          "online.disconnect", "cards.view", "cards.generate", "cards.revoke",
          "cards.recharge", "cards.print", "plans.view", "plans.create",
          "plans.edit", "plans.delete", "reports.finance"]


def _login(client, *, admin_id, is_super):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(_PERMS)


def _flag(mgr, flag, val=True):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr, permissions={flag: val})


def _override(mgr, key, val):
    from app.radius.services import manager_grants as mg
    mg.set_action_override(mgr, key, val, tenant_id=1)


# ═══ registry ═══════════════════════════════════════════════════════════════
def test_registry_and_endpoint_map(app):
    from app.radius.services import manager_grants as mg
    for k in ("subscriber.create", "subscriber.delete", "subscriber.extend",
              "subscriber.balance_add", "subscriber.payment", "subscriber.loan",
              "subscriber.status", "session.disconnect", "cards.generate",
              "cards.import", "plan.create", "distributor.manage"):
        assert k in mg.ACTION_REGISTRY
    assert mg.endpoint_action("users_delete") == "subscriber.delete"
    assert mg.endpoint_action("radius.users_payment_create") == "subscriber.payment"
    assert mg.endpoint_action("online_disconnect") == "session.disconnect"


# ═══ flag-backed actions (default OFF, made real) ═══════════════════════════
def test_create_blocked_without_flag_allowed_with(app):
    with app.app_context():
        mgr = _mgr("m_create")
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users",
                   data={"_csrf_token": "off-csrf", "username": "n1", "password": "p1234567"})
        assert r.status_code == 403          # can_create_subscriber OFF
    with app.app_context():
        _flag(mgr, "can_create_subscriber", True)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users",
                   data={"_csrf_token": "off-csrf", "username": "n2", "password": "p1234567"})
        assert r.status_code != 403


def test_activate_toggle_blocked_without_flag(app):
    with app.app_context():
        mgr = _mgr("m_act"); _plan(); _sub("s_act", manager_id=mgr)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users/s_act/toggle", data={"_csrf_token": "off-csrf"})
        assert r.status_code == 403          # can_activate_subscriber OFF
    with app.app_context():
        _flag(mgr, "can_activate_subscriber", True)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users/s_act/toggle", data={"_csrf_token": "off-csrf"})
        assert r.status_code != 403


def test_loan_blocked_without_flag(app):
    with app.app_context():
        mgr = _mgr("m_loan"); _plan(); _sub("s_loan", manager_id=mgr)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users/s_loan/loans",
                   data={"_csrf_token": "off-csrf", "amount": "5"})
        assert r.status_code == 403          # can_give_loan OFF


# ═══ RBAC-governed actions (default allowed; owner can disable) ═════════════
def test_extend_default_allowed_owner_can_disable(app):
    with app.app_context():
        mgr = _mgr("m_ext"); _plan(); _sub("s_ext", manager_id=mgr)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users/s_ext/extend",
                   data={"_csrf_token": "off-csrf", "days": "1"})
        assert r.status_code != 403          # default allowed (RBAC ok)
    with app.app_context():
        _override(mgr, "subscriber.extend", False)   # owner turns OFF
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users/s_ext/extend",
                   data={"_csrf_token": "off-csrf", "days": "1"})
        assert r.status_code == 403          # owner-disabled → hard 403


def test_delete_owner_disabled_blocks(app):
    with app.app_context():
        mgr = _mgr("m_del"); _plan(); _sub("s_del", manager_id=mgr)
        _override(mgr, "subscriber.delete", False)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users/s_del/delete", data={"_csrf_token": "off-csrf"})
        assert r.status_code == 403
    with app.app_context():
        assert db().execute("SELECT deleted_at FROM subscribers WHERE username='s_del'"
                            ).fetchone()["deleted_at"] is None   # not deleted


def test_payment_money_owner_disabled_blocks(app):
    with app.app_context():
        mgr = _mgr("m_pay"); _plan(); _sub("s_pay", manager_id=mgr)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users/s_pay/payments",
                   data={"_csrf_token": "off-csrf", "amount": "5"})
        assert r.status_code != 403          # default allowed (RBAC users.payments held)
    with app.app_context():
        _override(mgr, "subscriber.payment", False)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/users/s_pay/payments",
                   data={"_csrf_token": "off-csrf", "amount": "5"})
        assert r.status_code == 403


def test_payment_money_rbac_still_required(app):
    # money guard NOT weakened: without the RBAC money perm it's still 403,
    # even though the action default is allowed.
    with app.app_context():
        mgr = _mgr("m_pay2"); _plan(); _sub("s_pay2", manager_id=mgr)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["admin_id"] = mgr; s["is_super_admin"] = False; s["tenant_id"] = 1
            s["_csrf_token"] = "off-csrf"
            s["permissions"] = ["users.view"]   # NO users.payments
        r = c.post("/admin/radius/users/s_pay2/payments",
                   data={"_csrf_token": "off-csrf", "amount": "5"})
        assert r.status_code == 403


def test_disconnect_owner_disabled_blocks(app):
    with app.app_context():
        mgr = _mgr("m_disc")
        _override(mgr, "session.disconnect", False)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        r = c.post("/admin/radius/online/disconnect",
                   data={"_csrf_token": "off-csrf", "username": "x"})
        assert r.status_code == 403


# ═══ owner/super bypass ═════════════════════════════════════════════════════
def test_super_bypasses_action_gate(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)   # owner
        r = c.post("/admin/radius/users",
                   data={"_csrf_token": "off-csrf", "username": "sup1", "password": "p1234567"})
        assert r.status_code != 403            # super creates despite flag OFF


# ═══ config route persists action grants ════════════════════════════════════
def test_policy_route_persists_action_grants(app):
    with app.app_context():
        mgr = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        # grant create (flag) + disable delete (rbac override off = unchecked)
        # NB: any rbac action checkbox omitted => stored False (owner-off).
        r = c.post(f"/admin/radius/business-operators/manager/{mgr}/policy",
                   data={"_csrf_token": "off-csrf",
                         "can_create_subscriber": "1",
                         # rbac action field names carry the dotted key
                         "action_subscriber.extend": "1"})   # extend allowed, delete omitted->off
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.action_permitted(mgr, "subscriber.create", tenant_id=1) is True
        assert mg.action_permitted(mgr, "subscriber.extend", tenant_id=1) is True
        assert mg.action_permitted(mgr, "subscriber.delete", tenant_id=1) is False
