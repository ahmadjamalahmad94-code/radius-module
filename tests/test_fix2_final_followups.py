"""Fix wave 2 — final integration follow-ups (permguard + permmodel merged).

  a. ``/api/admin/me`` (and the login response) tell the app whether the admin
     IS a distributor login (``distributors.login_admin_id``, D11).
  b. ``sub_manager_delegate`` changes only what was sent — the child's limits,
     credit, profit share, approval threshold and other flags are KEPT — and a
     manager can never delegate more than he has (flags, actions, limits,
     credit), also at run time for the role-derived actions.
  c. The tools screen: a per-tool permission map (``grants.tools`` in
     ``/api/admin/me`` and ``GET /api/v1/tools``) from the API guard's own
     decision, so the app hides the owner-only tools.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest

PW = "Pass-12345"


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_f2final_")
    keys = ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER", "HOBERADIUS_NO_SEED",
            "HOBERADIUS_LICENSE_GATE_TEST_BYPASS")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password=PW,
                                 full_name="Owner", is_super_admin=True)
    yield flask_app
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


# NB: setup/assertions run inside ``with app.app_context()`` blocks and every
# request runs OUTSIDE them — a request inside an already-pushed app context
# shares ``flask.g`` (and its per-request permission caches) with the next one.


# ── helpers ──────────────────────────────────────────────────────────────
def _db():
    from app.radius.db.connection import db
    return db()


def _owner_id() -> int:
    from app.radius.db.repos import admins_repo
    return int(admins_repo.primary_admin_id())


def _role(perms):
    from app.radius.db.repos import admins_repo
    return admins_repo.create_role(name="r_" + uuid4().hex[:8], display_name="R",
                                   permissions=tuple(perms))


def _admin(perms=("users.view",), *, role_id=None):
    from app.radius.db.repos import admins_repo
    rid = role_id if role_id is not None else _role(perms).id
    return admins_repo.create_admin(username="m_" + uuid4().hex[:8], password=PW,
                                    full_name="M", is_super_admin=False, role_id=rid)


def _bearer(admin_id) -> dict:
    from app.radius.db.repos import api_tokens_repo
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name="t-" + uuid4().hex[:6], scopes=["admin:full"],
        created_by=int(admin_id))
    return {"Authorization": "Bearer " + plain}


def _login(client, admin_id):
    """A real login's session (same keys set_current_admin writes)."""
    from app.radius.auth.session_helpers import _resolve_is_super
    from app.radius.db.repos import admins_repo
    a = admins_repo.get_admin(int(admin_id))
    with client.session_transaction() as s:
        s["admin_id"] = a.id
        s["admin_user"] = a.username
        s["admin_name"] = a.username
        s["is_super_admin"] = bool(_resolve_is_super(a))
        s["tenant_id"] = 1
        s["admin_sv"] = admins_repo.session_epoch(a.id) or 0
        s["admin_av"] = admins_repo.authz_epoch(a.id)
        s["permissions"] = list(admins_repo.admin_permissions(a))
        s["_csrf_token"] = "tok"
    client.environ_base["HTTP_X_CSRFTOKEN"] = "tok"


def _svc():
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    return ManagerDistributorOpsService(tenant_id=1)


def _raw_policy(admin_id) -> dict:
    import json
    row = _db().execute(
        "SELECT permissions_json, limits_json, credit_limit_minor, profit_share_percent, "
        "require_approval_above_minor FROM manager_distributor_policies "
        "WHERE tenant_id=1 AND entity_type='manager' AND entity_id=?", (int(admin_id),)
    ).fetchone()
    assert row is not None
    return {"perms": json.loads(row["permissions_json"] or "{}"),
            "limits": json.loads(row["limits_json"] or "{}"),
            "credit_minor": int(row["credit_limit_minor"] or 0),
            "profit": float(row["profit_share_percent"] or 0),
            "approval_minor": int(row["require_approval_above_minor"] or 0)}


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    return body["data"]


# ═════════════════════ a. /api/admin/me: distributor login ═════════════════
def test_me_reports_a_distributor_login(app):
    from app.radius.db.repos import operations_repo
    with app.app_context():
        owner_mgr = _admin(("users.view", "cards.view"))
        dist_login = _admin(("cards.view",))
        dist = operations_repo.create_distributor(1, {
            "admin_id": owner_mgr.id,              # the OWNING manager (D11)
            "login_admin_id": dist_login.id,       # the account that IS the distributor
            "name": "dist_" + uuid4().hex[:6],
        }, actor="test")
        h_dist, h_mgr = _bearer(dist_login.id), _bearer(owner_mgr.id)
    c = app.test_client()
    me = _data(c.get("/api/admin/me", headers=h_dist))["admin"]
    assert me["is_distributor"] is True
    assert me["distributor_id"] == int(dist["id"])
    assert me["distributor_name"] == dist["name"]
    # the manager who OWNS the distributor is not a distributor login himself
    me2 = _data(c.get("/api/admin/me", headers=h_mgr))["admin"]
    assert me2["is_distributor"] is False and me2["distributor_id"] is None


def test_me_owner_and_plain_manager_are_not_distributors(app):
    with app.app_context():
        heads = [_bearer(_owner_id()), _bearer(_admin().id)]
    c = app.test_client()
    for h in heads:
        me = _data(c.get("/api/admin/me", headers=h))["admin"]
        assert me["is_distributor"] is False and me["distributor_id"] is None


def test_login_response_carries_the_distributor_fields(app):
    from app.radius.db.repos import operations_repo
    with app.app_context():
        dist_login = _admin(("cards.view",))
        dist = operations_repo.create_distributor(1, {
            "login_admin_id": dist_login.id, "name": "dist_" + uuid4().hex[:6]}, actor="test")
    res = app.test_client().post("/api/admin/login",
                                 json={"username": dist_login.username, "password": PW})
    admin = _data(res)["admin"]
    assert admin["is_distributor"] is True and admin["distributor_id"] == int(dist["id"])


# ═════════════════════ b. sub-manager delegation ═══════════════════════════
def _parent_with_child(parent_perms=("users.view", "users.delete", "store.review",
                                     "store.view")):
    from app.radius.db.repos import admins_repo
    role = _role(parent_perms)
    parent = _admin(role_id=role.id)
    _svc().update_policy(entity_type="manager", entity_id=parent.id,
                         permissions={"can_create_sub_managers": True,
                                      "can_manage_distributors": True})
    child = _admin(role_id=role.id)
    _db().execute("UPDATE admins SET parent_admin_id=? WHERE id=?", (parent.id, child.id))
    return admins_repo.get_admin(parent.id), admins_repo.get_admin(child.id)


def _delegate(app, parent_id, child_id, data):
    c = app.test_client()
    _login(c, parent_id)
    r = c.post(f"/admin/radius/business-operators/sub-managers/{child_id}/delegate",
               data={"_csrf_token": "tok", **data})
    assert r.status_code in (302, 303), r.status_code
    return r


def test_delegate_keeps_existing_limits_credit_and_other_flags(app):
    with app.app_context():
        parent, child = _parent_with_child()
        # the owner configured the child before: limits, credit, profit share,
        # approval threshold and a non-delegable flag (view all subscribers).
        _svc().set_policy(entity_type="manager", entity_id=child.id,
                          permissions={"can_view_all_subscribers": True},
                          limits={"max_subscribers": 50, "max_cards_daily": 20,
                                  "spend_cap_daily": "30.00"},
                          profit_share_percent=12.5, credit_limit="100",
                          require_approval_above="40")
        before = _raw_policy(child.id)
    _delegate(app, parent.id, child.id, {"flag_can_manage_distributors": "1"})
    with app.app_context():
        after = _raw_policy(child.id)
    assert after["limits"] == before["limits"]          # NOT reset to defaults
    assert after["credit_minor"] == before["credit_minor"] == 10000
    assert after["profit"] == 12.5
    assert after["approval_minor"] == before["approval_minor"] == 4000
    assert after["perms"]["can_view_all_subscribers"] is True
    assert after["perms"]["can_manage_distributors"] is True   # what was delegated


def test_delegate_changes_only_the_limits_sent_and_clamps_them(app):
    with app.app_context():
        parent, child = _parent_with_child()
        _svc().update_policy(entity_type="manager", entity_id=parent.id,
                             limits={"max_subscribers": 100, "max_cards_daily": 0,
                                     "spend_cap_daily": "50.00"},
                             credit_limit="200")
        _svc().update_policy(entity_type="manager", entity_id=child.id,
                             limits={"max_trial_days": 3})
    _delegate(app, parent.id, child.id, {
        "limit_max_subscribers": "500",     # above the parent → 100
        "limit_max_cards_daily": "7",       # parent unlimited → as asked
        "limit_spend_cap_daily": "0",       # «unlimited» under a capped parent → 50
        "credit_limit": "999"})             # above the parent → 200
    with app.app_context():
        pol = _raw_policy(child.id)
    lims = pol["limits"]
    assert lims["max_subscribers"] == 100
    assert lims["max_cards_daily"] == 7
    assert lims["spend_cap_daily"] == "50.00"
    assert lims["max_trial_days"] == 3                  # untouched
    assert pol["credit_minor"] == 20000


def test_delegate_cannot_escalate_flags(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        parent, child = _parent_with_child()
    _delegate(app, parent.id, child.id, {"flag_can_manage_distributors": "1",
                                         "flag_can_see_wholesale": "1",
                                         "flag_can_see_password": "1"})
    with app.app_context():
        perms = _raw_policy(child.id)["perms"]
        assert mg.can_see(child.id, "can_see_wholesale", tenant_id=1) is False
    assert perms["can_manage_distributors"] is True
    assert perms["can_see_wholesale"] is False and perms["can_see_password"] is False


def test_child_never_exceeds_parent_on_role_derived_actions(app):
    """The child inherits the parent's role, so the role-derived actions
    (delete, store approvals…) are capped at run time: what the owner switched
    off for the parent is off for the child too."""
    from app.radius.services import manager_grants as mg
    with app.app_context():
        parent, child = _parent_with_child()
        assert mg.action_permitted(child.id, "subscriber.delete", tenant_id=1) is True
        mg.set_action_override(parent.id, "subscriber.delete", False, tenant_id=1)
        mg.set_action_override(parent.id, "store.withdraw_approve", False, tenant_id=1)
    with app.app_context():
        assert mg.action_permitted(child.id, "subscriber.delete", tenant_id=1) is False
        assert mg.action_permitted(child.id, "store.withdraw_approve", tenant_id=1) is False
        assert mg.action_permitted(child.id, "store.deposit_approve", tenant_id=1) is True
    # the parent can switch a derived action off for the child explicitly
    _delegate(app, parent.id, child.id, {"action_store.deposit_approve": "0"})
    with app.app_context():
        assert mg.action_permitted(child.id, "store.deposit_approve", tenant_id=1) is False
        assert mg.action_permitted(parent.id, "store.deposit_approve", tenant_id=1) is True


def test_new_sub_manager_starts_within_the_parents_caps(app):
    from app.radius.db.repos import admins_repo
    with app.app_context():
        parent, _child = _parent_with_child()
        _svc().update_policy(entity_type="manager", entity_id=parent.id,
                             limits={"max_subscribers": 25}, credit_limit="60")
    c = app.test_client()
    _login(c, parent.id)
    uname = "sub_" + uuid4().hex[:6]
    r = c.post("/admin/radius/business-operators/sub-managers",
               data={"_csrf_token": "tok", "username": uname, "password": "sub-pass-123"})
    assert r.status_code in (302, 303)
    with app.app_context():
        kid = admins_repo.get_by_username(uname)
        assert kid is not None and kid.role_id == parent.role_id   # never above the parent
        pol = _raw_policy(kid.id)
    assert pol["limits"]["max_subscribers"] == 25
    assert pol["credit_minor"] == 6000


def test_owner_created_sub_manager_is_not_a_super_admin(app):
    """The owner's own role («مدير عام» or none) is no ceiling for a
    sub-manager: without a chosen parent it gets the least-privileged role."""
    from app.radius.db.repos import admins_repo
    with app.app_context():
        owner = _owner_id()
    c = app.test_client()
    _login(c, owner)
    uname = "osub_" + uuid4().hex[:6]
    c.post("/admin/radius/business-operators/sub-managers",
           data={"_csrf_token": "tok", "username": uname, "password": "sub-pass-123"})
    with app.app_context():
        kid = admins_repo.get_by_username(uname)
        assert kid is not None
        assert kid.role_id == admins_repo.least_privileged_role_id()


def test_update_policy_is_a_partial_merge(app):
    with app.app_context():
        m = _admin()
        _svc().set_policy(entity_type="manager", entity_id=m.id,
                          permissions={"can_see_profit": True},
                          limits={"max_subscribers": 9}, credit_limit="5",
                          profit_share_percent=3, require_approval_above="2")
        _svc().update_policy(entity_type="manager", entity_id=m.id,
                             permissions={"can_see_wholesale": True})
        pol = _raw_policy(m.id)
    assert pol["perms"] == {"can_see_profit": True, "can_see_wholesale": True}
    assert pol["limits"]["max_subscribers"] == 9
    assert (pol["credit_minor"], pol["profit"], pol["approval_minor"]) == (500, 3.0, 200)


def test_clamp_helpers_unit(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        assert mg.clamp_to_parent_cap(500, 100) == 100
        assert mg.clamp_to_parent_cap(0, 100) == 100          # «unlimited» → parent's cap
        assert mg.clamp_to_parent_cap(7, 0) == 7              # parent unlimited
        assert mg.clamp_to_parent_cap("٥٠", "") == 50          # Arabic digits, empty cap
        assert mg.clamp_delegated_limits(None, {"max_subscribers": "12"}) == {"max_subscribers": 12}


# ═════════════════════ c. tools permission map ═════════════════════════════
_OWNER_ONLY_TOOLS = {"set_speeds", "general_adjustments", "test_auth", "maintenance"}


def test_me_exposes_the_tools_map_for_a_limited_manager(app):
    with app.app_context():
        h = _bearer(_admin(("users.view", "reports.view")).id)
    c = app.test_client()
    tools = _data(c.get("/api/admin/me", headers=h))["grants"]["tools"]
    assert tools == {"set_speeds": False, "general_adjustments": False,
                     "test_auth": False, "radius_log": True, "maintenance": False}
    # the map matches what the server does
    assert c.post("/api/v1/tools/set-speeds", headers=h, json={}).status_code == 403
    assert c.post("/api/v1/tools/test-auth", headers=h, json={}).status_code == 403
    assert c.get("/api/v1/tools/radius-log", headers=h).status_code == 200


def test_tools_map_owner_and_super_role(app):
    from app.radius.db.repos import admins_repo
    with app.app_context():
        h_owner = _bearer(_owner_id())
        sa = admins_repo.get_role_by_name("super_admin")
        h_boss = _bearer(_admin(role_id=sa.id).id)
    c = app.test_client()
    tools = _data(c.get("/api/admin/me", headers=h_owner))["grants"]["tools"]
    assert all(tools.values()) and set(tools) >= _OWNER_ONLY_TOOLS | {"radius_log"}
    # «مدير عام» = every NON-owner permission → still no owner-only tool
    tools = _data(c.get("/api/admin/me", headers=h_boss))["grants"]["tools"]
    assert tools["radius_log"] is True
    assert not any(tools[k] for k in _OWNER_ONLY_TOOLS)


def test_tools_catalog_endpoint(app):
    with app.app_context():
        h = _bearer(_admin(("users.view",)).id)
        h_owner = _bearer(_owner_id())
    c = app.test_client()
    d = _data(c.get("/api/v1/tools", headers=h))
    by_key = {i["key"]: i for i in d["items"]}
    assert set(by_key) == _OWNER_ONLY_TOOLS | {"radius_log"}
    for k in _OWNER_ONLY_TOOLS:
        assert by_key[k]["owner_only"] is True and by_key[k]["allowed"] is False
    assert by_key["radius_log"]["owner_only"] is False
    assert by_key["radius_log"]["allowed"] is False            # no reports.view
    assert by_key["set_speeds"]["path"] == "/api/v1/tools/set-speeds"
    assert by_key["set_speeds"]["label"]
    d2 = _data(c.get("/api/v1/tools", headers=h_owner))
    assert all(i["allowed"] for i in d2["items"])
