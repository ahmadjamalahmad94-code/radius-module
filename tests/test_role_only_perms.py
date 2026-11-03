"""Owner 03/11 — permissions come from the job title (role) only.

  «حصرنا الحذف والتعديل للمالك، وتفعيله بالصلاحيات ما بيتجاوز ولا بتنفذ.
   بدنا نخلي الصلاحيات تنفرض وتتنفذ طالما تفعلت. والمالك بس صفة تحدد مالك
   الريدياس مش ينفرد بالصلاحيات… المدير بيورث المسمى الوظيفي فقط وما اله اي
   صلاحيات منفردة… انشاء مدير ينحصر بالرصيد ولأي جروب صلاحيات يتبع.»
  (ب) «الحدود الرقمية تضل لكل مدير بشكل منفصل.»

One app per module; every test creates its own admins/subscribers.
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
    tmp = tempfile.mkdtemp(prefix="hr_roleonly_")
    old = {k: os.environ.get(k) for k in ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER",
                                          "HOBERADIUS_NO_SEED")}
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


# ── helpers ──────────────────────────────────────────────────────────────
def _owner_id() -> int:
    from app.radius.db.repos import admins_repo
    return int(admins_repo.primary_admin_id())


def _super_role():
    from app.radius.core.constants import ROLE_SUPER_ADMIN
    from app.radius.db.repos import admins_repo
    return admins_repo.get_role_by_name(ROLE_SUPER_ADMIN)


def _role(perms, granular=None):
    from app.radius.db.repos import admins_repo
    r = admins_repo.create_role(name="r_" + uuid4().hex[:8], display_name="R",
                                permissions=tuple(perms))
    if granular is not None:
        admins_repo.set_role_granular(r.id, granular)
    return r


def _admin(role_id=None):
    from app.radius.db.repos import admins_repo
    return admins_repo.create_admin(username="m_" + uuid4().hex[:8], password=PW,
                                    full_name="M", is_super_admin=False, role_id=role_id)


def _sub(manager_id=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username="s_" + uuid4().hex[:8],
        password="p1234567", status="enabled", manager_id=manager_id))


def _login(client, admin_id):
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


def _decision(admin_id, endpoint, method="POST"):
    from app.radius.auth.session_helpers import _resolve_is_super
    from app.radius.db.repos import admins_repo
    from app.radius.routes.blueprint import rbac_denial_status
    a = admins_repo.get_admin(admin_id)
    return rbac_denial_status(endpoint, method, is_super=_resolve_is_super(a),
                              perms=admins_repo.admin_permissions(a), admin_id=admin_id,
                              tenant_id=1, record_activity=False)


def _deny_everything_for(admin_id):
    """What the old per-manager page could store: deny every action, lock every
    section, lock every field — none of it may count any more."""
    from app.radius.services import manager_grants as mg
    mg.set_section_access(admin_id, {n: "locked" for n in mg.section_names()}, tenant_id=1)
    mg.set_action_grants(admin_id, "_actions",
                         {k: False for k in mg.ACTION_REGISTRY}, tenant_id=1)
    for entity in mg.FIELD_REGISTRY:
        mg.set_field_grants(admin_id, entity, [], tenant_id=1)


# ── the owner's complaint ─────────────────────────────────────────────────
def test_a_general_manager_edits_disables_and_deletes_a_subscriber_the_owner_made(app):
    with app.app_context():
        mgr = _admin(_super_role().id)
        s1, s2 = _sub(manager_id=_owner_id()), _sub(manager_id=_owner_id())
    with app.test_client() as c:
        _login(c, mgr.id)
        r = c.post(f"/admin/radius/users/{s1.username}/toggle")
        assert r.status_code in (302, 303)
        r = c.post(f"/admin/radius/users/{s2.username}/delete")
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        assert subscribers_repo.get_subscriber(1, s1.username).status == "disabled"
        gone = subscribers_repo.get_subscriber(1, s2.username)
        assert gone is None or getattr(gone, "deleted_at", None)


def test_a_job_title_with_the_keys_is_enough_no_owner_flag_needed(app):
    with app.app_context():
        role = _role(["users.view", "users.edit", "users.delete", "users.change_status",
                      "scope.view_all_subscribers"])
        mgr = _admin(role.id)
        for ep in ("users_update", "users_delete", "users_toggle"):
            assert _decision(mgr.id, ep) is None, ep


# ── no individual permissions ────────────────────────────────────────────
def test_old_per_manager_denials_no_longer_count(app):
    with app.app_context():
        mgr = _admin(_super_role().id)
        _deny_everything_for(mgr.id)
        from app.radius.services import manager_grants as mg
        assert mg.action_permitted(mg_id := mgr.id, "subscriber.delete", tenant_id=1) is True
        assert mg.action_permitted(mg_id, "bulk.ops", tenant_id=1) is True
        assert mg.section_state(mg_id, "subscribers", tenant_id=1) != "locked"
        assert mg.reverted_attrs(mg_id, "subscriber", tenant_id=1) == set()
        assert _decision(mgr.id, "users_bulk_delete") is None


def test_old_per_manager_allowances_grant_nothing(app):
    with app.app_context():
        mgr = _admin(_role(["users.view"]).id)
        from app.radius.services import manager_grants as mg
        mg.set_action_grants(mgr.id, "_actions", {"bulk.ops": True}, tenant_id=1)
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        svc = ManagerDistributorOpsService(tenant_id=1)
        svc.set_policy(entity_type="manager", entity_id=mgr.id,
                       permissions={"can_view_all_subscribers": True,
                                    "can_manage_distributors": True})
        assert mg.action_permitted(mgr.id, "bulk.ops", tenant_id=1) is False
        assert svc.has_permission(entity_type="manager", entity_id=mgr.id,
                                  permission="can_view_all_subscribers") is False
        assert svc.has_permission(entity_type="manager", entity_id=mgr.id,
                                  permission="can_manage_distributors") is False


def test_section_lock_lives_on_the_job_title(app):
    with app.app_context():
        role = _role(["users.view", "users.create"],
                     granular={"section_access": {"subscribers": "locked"}})
        mgr = _admin(role.id)
        from app.radius.services import manager_grants as mg
        assert mg.section_state(mgr.id, "subscribers", tenant_id=1) == "locked"


def test_numeric_limits_stay_per_manager(app):
    """Owner's decision (ب): max subscribers, spend caps, daily rates stay each manager's own."""
    with app.app_context():
        mgr = _admin(_super_role().id)
        from app.radius.services import manager_grants as mg
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        ManagerDistributorOpsService(tenant_id=1).set_policy(
            entity_type="manager", entity_id=mgr.id,
            limits={"max_subscribers": 7, "rate_daily": {"subscriber.delete": 3}})
        mg._invalidate_cache()
        lims = mg._grants_row(mgr.id, 1)["limits"]
        assert lims["max_subscribers"] == 7
        assert lims["rate_daily"] == {"subscriber.delete": 3}


def test_the_manager_page_offers_no_individual_permissions(app):
    with app.app_context():
        mgr = _admin(_super_role().id)
    with app.test_client() as c:
        _login(c, _owner_id())
        page = c.get(f"/admin/radius/business-operators/manager/{mgr.id}").get_data(as_text=True)
    assert 'name="tri_' not in page
    assert 'name="section_' not in page
    assert 'name="field_control_' not in page
    assert "صلاحيات هذا المدير كلها من مسمّاه الوظيفي" in page


# ── owner-only actions became permissions ────────────────────────────────
def test_permanent_delete_follows_its_key_not_the_owner_flag(app):
    with app.app_context():
        holder = _admin(_role(["cards.view", "cards.purge"]).id)
        plain = _admin(_role(["cards.view", "cards.batch_ops"]).id)
        assert _decision(holder.id, "recycle_bin_purge") is None
        assert _decision(plain.id, "recycle_bin_purge") is not None
        from app.radius.db.repos import admins_repo
        from app.radius.services.front_owner import session_can_delete_permanently
        for a, want in ((holder, True), (plain, False)):
            with app.test_request_context():
                from flask import session
                session["admin_id"], session["tenant_id"] = a.id, 1
                session["is_super_admin"] = False
                session["permissions"] = list(admins_repo.admin_permissions(a))
                assert session_can_delete_permanently() is want


def test_general_manager_carries_the_new_keys(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        perms = set(admins_repo.admin_permissions(_admin(_super_role().id)))
        assert {"cards.purge", "admins.credit_caps"} <= perms


def test_credit_caps_follow_their_key(app):
    with app.app_context():
        target = _admin(_role(["users.view"]).id)
        caps = _admin(_role(["admins.view", "admins.edit", "admins.credit_caps"]).id)
        nocaps = _admin(_role(["admins.view", "admins.edit"]).id)
    form = {"full_name": "T", "role_id": str(target.role_id), "enabled": "1",
            "credit_caps_present": "1", "debt_cap_enabled": "1", "debt_cap_amount": "50"}
    with app.test_client() as c:
        _login(c, nocaps.id)
        assert c.post(f"/admin/radius/admins/{target.id}", data=form).status_code == 403
    with app.test_client() as c:
        _login(c, caps.id)
        assert c.post(f"/admin/radius/admins/{target.id}", data=form).status_code in (302, 303)


def test_restoring_an_archived_manager_needs_admins_edit_not_the_owner(app):
    with app.app_context():
        from app.radius.services.recycle_restore_policy import restore_denial, table_visible
        assert table_visible("admins", is_owner=False, perms={"admins.edit"}) is True
        assert table_visible("admins", is_owner=False, perms={"users.view"}) is False
        assert restore_denial("roles", 1, admin_id=5, is_owner=False,
                              perms={"admins.edit"}) is None


# ── creating and editing a manager ────────────────────────────────────────
def test_a_new_manager_needs_a_job_title(app):
    with app.test_client() as c:
        _login(c, _owner_id())
        r = c.post("/admin/radius/admins", data={
            "username": "nr_" + uuid4().hex[:6], "password": "Abc-123456", "enabled": "1"})
    assert r.status_code == 422
    assert "المسمّى الوظيفي" in r.get_data(as_text=True)


def test_a_new_manager_gets_its_opening_balance(app):
    name = "ob_" + uuid4().hex[:6]
    with app.app_context():
        rid = _super_role().id
    with app.test_client() as c:
        _login(c, _owner_id())
        r = c.post("/admin/radius/admins", data={
            "username": name, "password": "Abc-123456", "enabled": "1",
            "role_id": str(rid), "opening_balance": "25"})
    assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        a = admins_repo.get_by_username(name)
        assert a is not None and a.role_id == rid
        prof = ManagerDistributorOpsService(tenant_id=1).profile(
            entity_type="manager", entity_id=a.id)
        assert float(prof.get("wallet_balance") or prof.get("balance") or 0) == 25.0


def test_a_general_manager_may_appoint_a_general_manager_but_not_a_partner(app):
    with app.app_context():
        boss = _admin(_super_role().id)
        target = _admin(_role(["users.view"]).id)
        from app.radius.auth.owner import OwnerGuardError, assert_can_modify_admin
        assert_can_modify_admin(boss.id, target.id, new_role_id=_super_role().id,
                                super_change=True)
        with pytest.raises(OwnerGuardError):
            assert_can_modify_admin(boss.id, target.id, co_owner_change=True)
        weak = _admin(_role(["admins.view", "admins.edit"]).id)
        with pytest.raises(OwnerGuardError):                 # no escalation
            assert_can_modify_admin(weak.id, target.id, new_role_id=_super_role().id)


def test_saving_the_edit_form_never_demotes_a_general_manager(app):
    """The «سوبر يوزر» toggle is gone; its absence must not drop the role."""
    with app.app_context():
        mgr = _admin(_super_role().id)
        rid = mgr.role_id
    with app.test_client() as c:
        _login(c, _owner_id())
        r = c.post(f"/admin/radius/admins/{mgr.id}", data={
            "full_name": "Still super", "role_id": str(rid), "enabled": "1",
            "access_level_present": "1"})
    assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import admins_repo
        assert admins_repo.get_admin(mgr.id).role_id == rid
