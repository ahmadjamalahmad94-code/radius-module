"""permmodel (fix wave 2) — the grant model, the owner's three complaints and
the co-owner («شريك/مالك») feature.

  «افتح صلاحية إنشاء مشترك… عند الحفظ يقله غير مسموح ويطير كل البيانات»
  «أحط المدير يشوف مستخدمين المدراء الثانين برضو ما بشوف»
  «بدي أقدر بسهولة أعمل مدير سوبر يوزر أو أمنحه مالك… شركاء بالشبكة»

One app per module (fast); every test creates its own admins/subscribers.
"""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from uuid import uuid4

import pytest

PW = "Pass-12345"


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_permmodel_")
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
        # the ORIGINAL owner = lowest admin id
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
def _db():
    from app.radius.db.connection import db
    return db()


def _owner_id() -> int:
    """The ORIGINAL owner (lowest id — the bootstrap admin on a fresh DB)."""
    from app.radius.db.repos import admins_repo
    return int(admins_repo.primary_admin_id())


def _role(perms, granular=None, name=None):
    from app.radius.db.repos import admins_repo
    r = admins_repo.create_role(name=name or ("r_" + uuid4().hex[:8]),
                                display_name="R", permissions=tuple(perms))
    if granular is not None:
        admins_repo.set_role_granular(r.id, granular)
    return r


def _admin(role_id=None, *, name=None):
    from app.radius.db.repos import admins_repo
    return admins_repo.create_admin(username=name or ("m_" + uuid4().hex[:8]), password=PW,
                                    full_name="M", is_super_admin=False, role_id=role_id)


def _sub(username=None, *, manager_id=None, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username or ("s_" + uuid4().hex[:8]),
        password="p1234567", status="enabled", manager_id=manager_id, **kw))


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
    client.environ_base["HTTP_X_CSRFTOKEN"] = "tok"   # the app's own CSRF check


def _bearer(admin_id) -> dict:
    from app.radius.db.repos import api_tokens_repo
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name="t-" + uuid4().hex[:6], scopes=["admin:full"],
        created_by=int(admin_id))
    return {"Authorization": "Bearer " + plain}


class _FormGrab(HTMLParser):
    """Collects the submittable fields of the form with a given data-testid —
    exactly what the browser would POST when the owner presses «حفظ»."""

    def __init__(self, testid):
        super().__init__()
        self.testid, self.inside, self.fields = testid, False, []
        self._sel = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("data-testid") == self.testid:
            self.inside = True
            return
        if not self.inside:
            return
        if tag == "input":
            t = (a.get("type") or "text").lower()
            if not a.get("name") or "disabled" in a:
                return
            if t in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields.append((a["name"], a.get("value") or "on"))
            elif t not in ("submit", "button", "file"):
                self.fields.append((a["name"], a.get("value") or ""))
        elif tag == "select" and a.get("name"):
            self._sel = a["name"]
        elif tag == "option" and self._sel and "selected" in a:
            self.fields.append((self._sel, a.get("value") or ""))

    def handle_endtag(self, tag):
        if tag == "form" and self.inside:
            self.inside = False
        if tag == "select":
            self._sel = None


def _form_fields(html: str, testid: str):
    p = _FormGrab(testid)
    p.feed(html)
    return p.fields


def _effective(admin_id):
    """Every effective grant of a manager (actions + flags + sections)."""
    from app.radius.services import manager_grants as mg
    from app.radius.services.manager_distributor_ops import (
        DEFAULT_PERMISSIONS, ManagerDistributorOpsService)
    svc = ManagerDistributorOpsService(tenant_id=1)
    with_req = {}
    for k in mg.ACTION_REGISTRY:
        with_req["a:" + k] = mg.action_permitted(admin_id, k, tenant_id=1)
    for f in DEFAULT_PERMISSIONS:
        with_req["f:" + f] = svc.has_permission(entity_type="manager", entity_id=admin_id,
                                                permission=f)
    for s in mg.MANAGER_SECTION_REGISTRY:
        with_req["s:" + s] = mg.section_state(admin_id, s, tenant_id=1)
    return with_req


_OPERATOR = ["users.view", "users.create", "users.edit", "users.delete",
             "users.change_status", "users.extend", "users.change_plan", "users.quota",
             "users.balance_add", "users.payments", "users.send_message",
             "cards.view", "cards.generate", "cards.revoke", "cards.batch_ops",
             "cards.recharge", "cards.print", "plans.view", "plans.create",
             "plans.edit", "plans.delete", "admins.view", "admins.policy"]


def _fresh(fn):
    """Run ``fn`` inside a request context (g caches are per request)."""
    def run(app, *a, **kw):
        with app.test_request_context():
            return fn(*a, **kw)
    return run


# ═══ D01 — saving the manager page / the role page changes NOTHING ═══════════
def test_d01_saving_unchanged_manager_page_keeps_every_effective_permission(app):
    with app.app_context():
        role = _role(_OPERATOR, {"flags": {"can_see_balance": True}})
        mgr = _admin(role.id)
        owner = _owner_id()
    before = _fresh(_effective)(app, mgr.id)
    assert before["a:subscriber.delete"] and before["a:subscriber.extend"]
    with app.test_client() as c:
        _login(c, owner)
        page = c.get(f"/admin/radius/business-operators/manager/{mgr.id}")
        assert page.status_code == 200
        fields = _form_fields(page.get_data(as_text=True), "operator-policy-form")
        assert fields, "the policy form must render"
        from werkzeug.datastructures import MultiDict
        r = c.post(f"/admin/radius/business-operators/manager/{mgr.id}/policy",
                   data=MultiDict(fields))
        assert r.status_code in (302, 303)
    after = _fresh(_effective)(app, mgr.id)
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not changed, changed
    # and nothing RBAC-derived was stored as an override
    with app.app_context():
        from app.radius.services import manager_grants as mg
        row = _db().execute("SELECT action_grants_json FROM manager_distributor_policies "
                            "WHERE entity_type='manager' AND entity_id=?", (mgr.id,)).fetchone()
        stored = (row and row["action_grants_json"]) or "{}"
        for k in mg.derived_action_keys():
            assert f'"{k}"' not in stored, k


def test_d01_saving_unchanged_role_page_keeps_every_effective_permission(app):
    with app.app_context():
        role = _role(_OPERATOR, {"action_grants": {"_actions": {"storeuser.create": True}}})
        mgr = _admin(role.id)
        owner = _owner_id()
    before = _fresh(_effective)(app, mgr.id)
    with app.test_client() as c:
        _login(c, owner)
        page = c.get(f"/admin/radius/roles/{role.id}/edit")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        # the grants form on the merged page posts to roles_grants_save
        m = re.search(r'<form[^>]+action="([^"]*/roles/%d/grants)"' % role.id, html)
        assert m, "grants form missing"
        start = html.find(m.group(0))
        chunk = html[start:html.find("</form>", start)]
        chunk = chunk.replace(m.group(0), m.group(0).replace("<form", '<form data-testid="g"'))
        fields = _form_fields(chunk + "</form>", "g")
        from werkzeug.datastructures import MultiDict
        r = c.post(m.group(1), data=MultiDict(fields))
        assert r.status_code in (302, 303)
    after = _fresh(_effective)(app, mgr.id)
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not changed, changed


def test_d01_d02_repair_migration_removes_corrupt_values(app):
    """Migration 183 repairs servers already corrupted by the old save/read."""
    with app.app_context():
        role = _role(["users.view", "users.delete", "scope.view_all_subscribers"],
                     {"action_grants": {"_actions": {"subscriber.delete": False,
                                                     "bulk.ops": True}}})
        mgr = _admin(role.id)
        other = _admin(role.id)
        import json
        full_false = {k: False for k in (
            "can_create_batch", "can_create_subscriber", "can_activate_subscriber",
            "can_give_free_days", "can_give_trial_days", "can_give_loan",
            "can_manage_distributors", "can_view_all_subscribers",
            "can_view_all_card_batches", "can_import_batches", "can_see_wholesale",
            "can_see_password", "can_create_sub_managers", "can_see_balance",
            "can_see_profit")}
        for aid, extra in ((mgr.id, {"can_see_profit": True}), (other.id, {})):
            _db().execute(
                "INSERT INTO manager_distributor_policies(tenant_id, entity_type, entity_id,"
                " permissions_json, limits_json, action_grants_json, status, created_at, updated_at)"
                " VALUES(1,'manager',?,?,?,?,'active',datetime('now'),datetime('now'))",
                (aid, json.dumps({**full_false, **extra}),
                 json.dumps({"max_subscribers": 0, "credit_limit": "0.00",
                             "loan_wallet_deducted": True, "rate_daily": {},
                             "grants_expire_at": "", "spend_cap_daily": "0.00",
                             "spend_cap_monthly": "0.00"}),
                 json.dumps({"_actions": {"subscriber.extend": False}})))
        sql = io.open(os.path.join(os.path.dirname(__file__), "..", "app", "radius", "db",
                                   "migrations", "183_repair_grants_d01_d02.sql"),
                      encoding="utf-8").read()
        _db().executescript(sql)
        # manager with an explicit True keeps the True, loses the False dump
        row = _db().execute("SELECT permissions_json, action_grants_json FROM "
                            "manager_distributor_policies WHERE entity_type='manager' "
                            "AND entity_id=?", (mgr.id,)).fetchone()
        assert json.loads(row["permissions_json"]) == {"can_see_profit": True}
        assert "subscriber.extend" not in row["action_grants_json"]
        # an all-default row (created by merely OPENING the profile) is gone
        assert _db().execute("SELECT 1 FROM manager_distributor_policies WHERE "
                             "entity_type='manager' AND entity_id=?", (other.id,)).fetchone() is None
        blob = json.loads(_db().execute("SELECT granular_grants_json FROM roles WHERE id=?",
                                        (role.id,)).fetchone()[0])
        assert "subscriber.delete" not in blob["action_grants"]["_actions"]
        assert blob["action_grants"]["_actions"]["bulk.ops"] is True
    eff = _fresh(_effective)(app, other.id)
    assert eff["a:subscriber.delete"] is True
    assert eff["f:can_view_all_subscribers"] is True      # role key, no override now


# ═══ D02/D10 — opening a manager's profile writes nothing ═════════════════════
def test_d02_opening_profile_does_not_freeze_role_view_all(app):
    with app.app_context():
        role = _role(["users.view", "admins.view", "scope.view_all_subscribers"])
        mgr = _admin(role.id)
        owner = _owner_id()
    with app.test_client() as c:
        _login(c, owner)
        assert c.get(f"/admin/radius/business-operators/manager/{mgr.id}").status_code == 200
    with app.app_context():
        row = _db().execute("SELECT permissions_json FROM manager_distributor_policies "
                            "WHERE entity_type='manager' AND entity_id=?", (mgr.id,)).fetchone()
        assert row is None or row["permissions_json"] in ("{}", "", None)
    assert _fresh(_effective)(app, mgr.id)["f:can_view_all_subscribers"] is True


def test_d02_override_beats_role_beats_default(app):
    with app.app_context():
        role = _role(["users.view", "scope.view_all_subscribers"])
        mgr = _admin(role.id)
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        svc = ManagerDistributorOpsService(tenant_id=1)
        svc.set_policy(entity_type="manager", entity_id=mgr.id,
                       permissions={"can_view_all_subscribers": False})
        assert svc.has_permission(entity_type="manager", entity_id=mgr.id,
                                  permission="can_view_all_subscribers") is False
        svc.set_policy(entity_type="manager", entity_id=mgr.id, permissions={})
        assert svc.has_permission(entity_type="manager", entity_id=mgr.id,
                                  permission="can_view_all_subscribers") is True
        plain = _admin(_role(["users.view"]).id)
        assert svc.has_permission(entity_type="manager", entity_id=plain.id,
                                  permission="can_view_all_subscribers") is False


# ═══ D15 — a granted RBAC key works without a hidden gate ═════════════════════
def test_d15_online_disconnect_key_is_enough(app):
    with app.app_context():
        mgr = _admin(_role(["online.view", "online.disconnect", "online.lock_mac",
                            "users.temp_speed", "store.user_add", "users.send_message"]).id)
    def check():
        from app.radius.services import manager_grants as mg
        for k in ("session.disconnect", "session.lock_mac", "session.temp_speed",
                  "storeuser.create", "comms.sms"):
            assert mg.action_permitted(mgr.id, k, tenant_id=1) is True, k
        assert mg.action_permitted(mgr.id, "session.lock_ip", tenant_id=1) is False
        # D26: cancel from the profile = same gate as the online page
        assert mg.endpoint_action("users_temp_speed_cancel") == "session.temp_speed"
    _fresh(check)(app)


# ═══ Owner complaint (a) — «يفتح النموذج… عند الحفظ غير مسموح» ═════════════════
def _new_sub_form(username, **extra):
    data = {"username": username, "password": "abcd1234", "status": "enabled",
            "full_name": "Typed Name", "mobile": "0599000111"}
    data.update(extra)
    return data


def test_e2e_manager_with_only_users_create_opens_saves_and_row_exists(app):
    with app.app_context():
        mgr = _admin(_role(["users.view", "users.create"]).id)
    uname = "c_" + uuid4().hex[:6]
    with app.test_client() as c:
        _login(c, mgr.id)
        assert c.get("/admin/radius/users/new").status_code == 200
        r = c.post("/admin/radius/users", data=_new_sub_form(uname))
        assert r.status_code in (302, 303), r.get_data(as_text=True)[:400]
    with app.app_context():
        row = _db().execute("SELECT manager_id FROM subscribers WHERE username=?",
                            (uname,)).fetchone()
        assert row is not None


def test_e2e_locked_section_hides_button_and_refuses_before_opening(app):
    with app.app_context():
        mgr = _admin(_role(["users.view", "users.create", "users.edit"]).id)
        from app.radius.services import manager_grants as mg
        mg.set_section_access(mgr.id, {"subscribers": "locked"}, tenant_id=1)
    with app.test_client() as c:
        _login(c, mgr.id)
        r = c.get("/admin/radius/users/new")                       # refused BEFORE it opens
        assert r.status_code == 403
        assert "مقفول" in r.get_data(as_text=True)
        lst = c.get("/admin/radius/users")
        assert lst.status_code == 200
        html = lst.get_data(as_text=True)
        assert 'href="/admin/radius/users/new"' not in html     # list + sidebar
        dash = c.get("/admin/radius/")
        if dash.status_code == 200:
            assert 'href="/admin/radius/users/new"' not in dash.get_data(as_text=True)


def test_d04_refused_post_rerenders_form_with_typed_data(app):
    with app.app_context():
        mgr = _admin(_role(["users.view", "users.create"]).id)
        from app.radius.services import manager_grants as mg
        mg.set_section_access(mgr.id, {"subscribers": "locked"}, tenant_id=1)
    with app.test_client() as c:
        _login(c, mgr.id)
        r = c.post("/admin/radius/users", data=_new_sub_form("kept_" + uuid4().hex[:5],
                                                             full_name="اسم محفوظ"))
        assert r.status_code == 403
        html = r.get_data(as_text=True)
        assert "اسم محفوظ" in html and "0599000111" in html   # typed data kept
        assert "لم يُحفَظ" in html                             # Arabic reason
        assert 'name="username"' in html                        # it IS the form


def test_d05_revoke_takes_effect_on_next_request(app):
    with app.app_context():
        role = _role(["users.view", "users.create"])
        mgr = _admin(role.id)
    with app.test_client() as c:
        _login(c, mgr.id)
        assert c.get("/admin/radius/users/new").status_code == 200
        with app.app_context():
            from app.radius.db.repos import admins_repo
            admins_repo.update_role(role.id, permissions=("users.view",))
        assert c.get("/admin/radius/users/new").status_code == 403
        with app.app_context():
            from app.radius.db.repos import admins_repo
            admins_repo.update_role(role.id, permissions=("users.view", "users.create"))
        assert c.get("/admin/radius/users/new").status_code == 200


# ═══ Owner complaint (b) — view-all sees other managers' subscribers ═══════════
def test_e2e_view_all_manager_sees_others_in_list_360_and_api(app):
    with app.app_context():
        a = _admin(_role(["users.view", "users.edit", "scope.view_all_subscribers"]).id)
        b = _admin(_role(["users.view"]).id)
        theirs = _sub(manager_id=b.id)
    with app.test_client() as c:
        _login(c, a.id)
        assert theirs.username in c.get("/admin/radius/users?q=" + theirs.username).get_data(as_text=True)
        assert c.get(f"/admin/radius/users/{theirs.username}/profile").status_code == 200
        api = c.get("/api/v1/accounts?q=" + theirs.username, headers=_bearer(a.id))
        assert api.status_code == 200
        assert theirs.username in {i["username"] for i in api.get_json()["data"]["items"]}
        assert c.get(f"/api/v1/accounts/{theirs.username}", headers=_bearer(a.id)).status_code == 200


def test_d09_without_view_all_other_managers_subscribers_are_out_of_scope(app):
    with app.app_context():
        role = _role(["users.view", "users.edit", "users.delete", "users.extend"])
        a = _admin(role.id)
        b = _admin(role.id)
        mine = _sub(manager_id=a.id)
        theirs = _sub(manager_id=b.id)
    with app.test_client() as c:
        _login(c, a.id)
        assert c.get(f"/admin/radius/users/{mine.username}/profile").status_code == 200
        for url in (f"/admin/radius/users/{theirs.username}/profile",
                    f"/admin/radius/users/{theirs.username}/360",
                    f"/admin/radius/users/{theirs.username}/edit",
                    f"/admin/radius/subscribers/{theirs.id}"):
            assert c.get(url).status_code == 403, url
        assert c.post(f"/admin/radius/users/{theirs.username}/delete").status_code == 403
        assert c.post("/admin/radius/users/bulk-delete",
                      data={"usernames": [mine.username, theirs.username]}).status_code == 403
        hdr = _bearer(a.id)
        items = c.get("/api/v1/accounts?per_page=500", headers=hdr).get_json()["data"]["items"]
        names = {i["username"] for i in items}
        assert mine.username in names and theirs.username not in names
        for resp in (c.get(f"/api/v1/accounts/{theirs.username}", headers=hdr),
                     c.patch(f"/api/v1/accounts/{theirs.username}", headers=hdr,
                             json={"full_name": "x"}),
                     c.delete(f"/api/v1/accounts/{theirs.username}", headers=hdr),
                     c.get(f"/api/v1/accounts/{theirs.username}/actions-context", headers=hdr),
                     c.post(f"/api/v1/accounts/{theirs.username}/extend", headers=hdr,
                            json={"mode": "duration", "minutes": 60, "charge_mode": "free"})):
            assert resp.status_code == 403, resp.get_json()
    with app.app_context():
        assert _db().execute("SELECT deleted_at FROM subscribers WHERE id=?",
                             (theirs.id,)).fetchone()["deleted_at"] is None


def test_d11_owning_manager_is_not_the_distributor(app):
    with app.app_context():
        mgr = _admin(_role(["users.view"]).id)
        mine = _sub(manager_id=mgr.id)
        from app.radius.db.repos import operations_repo
        operations_repo.create_distributor(1, {"admin_id": mgr.id,
                                               "name": "d_" + uuid4().hex[:6]}, actor="t")
    with app.test_client() as c:
        r = c.get(f"/api/v1/accounts/{mine.username}", headers=_bearer(mgr.id))
        assert r.status_code == 200, r.get_json()


# ═══ Owner complaint (c) — super user / co-owner ═══════════════════════════════
def _decision(admin_id, endpoint, method="POST"):
    from app.radius.auth.session_helpers import _resolve_is_super
    from app.radius.db.repos import admins_repo
    from app.radius.routes.blueprint import rbac_denial_status
    a = admins_repo.get_admin(admin_id)
    return rbac_denial_status(endpoint, method, is_super=_resolve_is_super(a),
                              perms=admins_repo.admin_permissions(a), admin_id=admin_id,
                              tenant_id=1, record_activity=False)


def test_e2e_co_owner_manages_everything_but_cannot_demote_original_owner(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        co = _admin(_role(["users.view"]).id)
        owner = _owner_id()
    # the owner grants the co-owner from the web admin edit page
    with app.test_client() as c:
        _login(c, owner)
        page = c.get(f"/admin/radius/admins/{co.id}/edit").get_data(as_text=True)
        assert "منح صلاحيات المالك (شريك)" in page and "سوبر يوزر" in page
        r = c.post(f"/admin/radius/admins/{co.id}", data={
            "full_name": "Co", "role_id": str(co.role_id), "enabled": "1",
            "access_level_present": "1", "is_co_owner": "1"})
        assert r.status_code in (302, 303)
    with app.app_context():
        assert admins_repo.is_co_owner(co.id)
        for ep in ("admins_create", "admins_update", "admins_delete", "roles_create",
                   "roles_save", "roles_delete", "finance_ledger_void", "backups_run",
                   "data_reset_run", "settings_rotate_store_key"):
            assert _decision(co.id, ep) is None, ep
        for ep in ("roles_list", "tok_list", "backups"):
            assert _decision(co.id, ep, "GET") is None, ep
    with app.test_client() as c:
        _login(c, co.id)
        assert c.get("/admin/radius/settings/system").status_code in (200, 302)
        # cannot demote / disable / delete the ORIGINAL owner
        r = c.post(f"/admin/radius/admins/{owner}", data={
            "full_name": "x", "enabled": "", "access_level_present": "1"})
        assert r.status_code in (302, 303)
        c.post(f"/admin/radius/admins/{owner}/delete")
        hdr = _bearer(co.id)
        assert c.patch(f"/api/v1/admins/{owner}", headers=hdr,
                       json={"enabled": False}).status_code == 403
        assert c.patch(f"/api/v1/admins/{owner}", headers=hdr,
                       json={"password": "hijack-1234"}).status_code == 403
        assert c.delete(f"/api/v1/admins/{owner}", headers=hdr).status_code == 403
    with app.app_context():
        o = admins_repo.get_admin(owner)
        assert o is not None and o.enabled and o.deleted_at is None


def test_co_owner_only_granted_by_owner_like(app):
    with app.app_context():
        sup = _admin(None)
        from app.radius.db.repos import admins_repo
        from app.radius.core.constants import ROLE_SUPER_ADMIN
        admins_repo.update_admin(sup.id, role_id=admins_repo.get_role_by_name(ROLE_SUPER_ADMIN).id)
        victim = _admin(_role(["users.view"]).id)
    with app.test_client() as c:
        hdr = _bearer(sup.id)
        r = c.patch(f"/api/v1/admins/{victim.id}", headers=hdr, json={"is_co_owner": True})
        assert r.status_code == 403
        r = c.patch(f"/api/v1/admins/{sup.id}", headers=hdr, json={"is_co_owner": True})
        assert r.status_code == 403
    with app.app_context():
        assert not admins_repo.is_co_owner(victim.id) and not admins_repo.is_co_owner(sup.id)


def test_super_admin_role_is_all_non_owner_permissions(app):
    with app.app_context():
        from app.radius.core.constants import ALL_PERMISSIONS, ROLE_SUPER_ADMIN
        from app.radius.db.repos import admins_repo
        sup = _admin(admins_repo.get_role_by_name(ROLE_SUPER_ADMIN).id)
        assert set(admins_repo.admin_permissions(sup)) == set(ALL_PERMISSIONS)
        # manage managers + roles: no 403
        for ep in ("admins_create", "admins_update", "roles_create", "roles_save"):
            assert _decision(sup.id, ep) is None, ep
        assert _decision(sup.id, "roles_list", "GET") is None
        # deliberately owner-only
        for ep in ("finance_ledger_void", "backups_run", "data_reset_run"):
            assert _decision(sup.id, ep) == 403, ep
    def grants():
        from app.radius.services import manager_grants as mg
        for k in mg.ACTION_REGISTRY:
            assert mg.action_permitted(sup.id, k, tenant_id=1), k
        from app.radius.services.subscriber_scope import can_view_all_subscribers
        assert can_view_all_subscribers(sup.id, tenant_id=1)
    _fresh(grants)(app)
    with app.test_client() as c:
        hdr = _bearer(sup.id)
        assert c.get("/api/v1/admins", headers=hdr).status_code == 200
        r = c.post("/api/v1/roles", headers=hdr, json={"name": "r_" + uuid4().hex[:5],
                                                       "permissions": ["users.view"]})
        assert r.status_code == 201, r.get_json()


def test_non_owner_cannot_assign_a_role_above_himself(app):
    with app.app_context():
        mid = _admin(_role(["admins.view", "admins.create", "admins.edit", "users.view"]).id)
        big = _role(["users.view", "users.delete"])
    with app.test_client() as c:
        hdr = _bearer(mid.id)
        r = c.post("/api/v1/admins", headers=hdr, json={
            "username": "esc_" + uuid4().hex[:5], "password": PW, "role_id": big.id})
        assert r.status_code == 403
        r = c.post("/api/v1/admins", headers=hdr, json={
            "username": "esc_" + uuid4().hex[:5], "password": PW, "is_super_admin": True})
        assert r.status_code == 403


def test_raw_is_super_admin_flag_no_longer_unlocks_admin_management(app):
    with app.app_context():
        flag = _admin(_role(["users.view"]).id)
        from app.radius.db.repos import admins_repo
        admins_repo.update_admin(flag.id, is_super_admin=True)
    with app.test_client() as c:
        hdr = _bearer(flag.id)
        assert c.get("/api/v1/admins", headers=hdr).status_code == 403
        assert c.patch(f"/api/v1/admins/{_owner_id()}", headers=hdr,
                       json={"full_name": "pwned"}).status_code == 403


# ═══ D17/D19/D20/D21/D22/D23/D24 ══════════════════════════════════════════════
def test_d17_list_buttons_follow_actions_context(app):
    with app.app_context():
        mgr = _admin(_role(["users.view"]).id)
        mine = _sub(manager_id=mgr.id)
    with app.test_client() as c:
        _login(c, mgr.id)
        html = c.get("/admin/radius/users").get_data(as_text=True)
        assert mine.username in html
        form_tag = re.search(r'<form[^>]*action="/admin/radius/users/%s/delete"[^>]*>' % mine.username, html)
        assert form_tag and "data-perm-denied" in form_tag.group(0)
        ctx = c.get(f"/api/v1/accounts/{mine.username}/actions-context", headers=_bearer(mgr.id))
        assert ctx.get_json()["data"]["permissions"]["delete"] is False


def test_d19_field_grants_apply_on_create_web_and_api(app):
    with app.app_context():
        mgr = _admin(_role(["users.view", "users.create"]).id)
        from app.radius.services import manager_grants as mg
        mg.set_field_grants(mgr.id, "subscriber", ["name"], tenant_id=1)
        owner = _owner_id()
    web_name = "w_" + uuid4().hex[:6]
    with app.test_client() as c:
        _login(c, mgr.id)
        c.post("/admin/radius/users", data=_new_sub_form(
            web_name, manager_id=str(owner), custom_price="555", status="disabled"))
        hdr = _bearer(mgr.id)
        r = c.post("/api/v1/accounts", headers=hdr, json={
            "username": "a_" + uuid4().hex[:6], "password": "abcd1234", "balance": 1000})
        assert r.status_code == 403
        api_name = "a_" + uuid4().hex[:6]
        r = c.post("/api/v1/accounts", headers=hdr, json={
            "username": api_name, "password": "abcd1234", "manager_id": owner,
            "custom_price": 555, "status": "disabled"})
        assert r.status_code == 201, r.get_json()
    with app.app_context():
        for n in (web_name, api_name):
            row = _db().execute("SELECT manager_id, custom_price, status, balance FROM "
                                "subscribers WHERE username=?", (n,)).fetchone()
            assert row is not None, n
            assert int(row["manager_id"]) == mgr.id
            assert float(row["custom_price"] or 0) == 0
            assert row["status"] == "enabled"
            assert float(row["balance"] or 0) == 0


def test_d20_locked_fields_render_readonly_with_hint_and_warn_on_save(app):
    with app.app_context():
        mgr = _admin(_role(["users.view", "users.edit"]).id)
        mine = _sub(manager_id=mgr.id)
        from app.radius.services import manager_grants as mg
        mg.set_field_grants(mgr.id, "subscriber", ["name"], tenant_id=1)
    with app.test_client() as c:
        _login(c, mgr.id)
        html = c.get(f"/admin/radius/users/{mine.username}/edit").get_data(as_text=True)
        assert "data-field-locked" in html and '"custom_price"' in html
        assert "field-lock-hint" in html


def test_d21_rename_is_grantable_under_field_control(app):
    with app.app_context():
        mgr = _admin(_role(["users.view", "users.edit"]).id)
        from app.radius.services import manager_grants as mg
        assert "username" in mg.field_keys("subscriber")
        mg.set_field_grants(mgr.id, "subscriber", ["name"], tenant_id=1)
    def check(granted):
        from app.radius.services import manager_grants as mg
        from app.radius.services import subscriber_actions as sa
        mg.set_field_grants(mgr.id, "subscriber", granted, tenant_id=1)
        caller = sa.ActionCaller(tenant_id=1, admin_id=mgr.id, is_super=False, actor="t")
        return sa.username_rename_locked(caller)
    assert _fresh(check)(app, ["name"]) is True
    assert _fresh(check)(app, ["name", "username"]) is False


def test_d22_role_in_use_cannot_be_deleted(app):
    with app.app_context():
        role = _role(["users.view"])
        _admin(role.id)
        owner = _owner_id()
    with app.test_client() as c:
        _login(c, owner)
        r = c.post(f"/admin/radius/roles/{role.id}/delete")
        assert r.status_code == 409
        r = c.delete(f"/api/v1/roles/{role.id}", headers=_bearer(owner))
        assert r.status_code == 409
        assert r.get_json()["error"]["details"]["admins_count"] == 1
    with app.app_context():
        from app.radius.db.repos import admins_repo
        assert admins_repo.get_role(role.id) is not None


def test_d23_manager_policy_form_has_no_duplicate_checkbox_names(app):
    with app.app_context():
        mgr = _admin(_role(["users.view"]).id)
        owner = _owner_id()
    with app.test_client() as c:
        _login(c, owner)
        html = c.get(f"/admin/radius/business-operators/manager/{mgr.id}").get_data(as_text=True)
    names = re.findall(r'<input type="checkbox" name="([^"]+)"', html)
    dup = {n for n in names if names.count(n) > 1 and not n.startswith("section_")}
    assert not dup, dup


def test_d24_forbidden_names_the_real_reason(app):
    with app.app_context():
        mgr = _admin(_role(["users.view", "users.extend"]).id)
        mine = _sub(manager_id=mgr.id)
        from app.radius.services import manager_grants as mg
        mg.set_section_access(mgr.id, {"subscribers": "locked"}, tenant_id=1)
    with app.test_client() as c:
        _login(c, mgr.id)
        r = c.post(f"/admin/radius/users/{mine.username}/extend",
                   headers={"X-Requested-With": "fetch"}, data={"minutes": "60"})
        assert r.status_code == 403
        body = r.get_json()
        assert "مقفول" in body.get("detail", "")
        assert body.get("permission") in (None, "")          # NOT «users.extend»


# ═══ app contract ═════════════════════════════════════════════════════════════
def test_api_admin_me_contract(app):
    with app.app_context():
        mgr = _admin(_role(["users.view", "users.create"]).id)
        from app.radius.db.repos import admins_repo
        admins_repo.set_co_owner(_admin(None).id, False)
    with app.test_client() as c:
        me = c.get("/api/admin/me", headers=_bearer(mgr.id)).get_json()["data"]
        assert set(me["permissions"]) == {"users.view", "users.create"}
        assert me["admin"]["is_co_owner"] is False and me["admin"]["is_owner"] is False
        g = me["grants"]
        assert g["actions"]["subscriber.create"] is True
        assert g["actions"]["subscriber.delete"] is False
        assert g["view_all_subscribers"] is False
        assert g["sections"]["subscribers"] in ("open", "locked", "hidden")
        own = c.get("/api/admin/me", headers=_bearer(_owner_id())).get_json()["data"]
        assert own["admin"]["is_owner"] is True and own["admin"]["is_original_owner"] is True
        assert own["grants"]["view_all_subscribers"] is True
