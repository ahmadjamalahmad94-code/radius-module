"""fix wave 2 — owner follow-up on per-manager permissions.

  1. The manager page shows EVERY fine-grained action and flag as a three-way
     choice «حسب الدور» / «مسموح» / «ممنوع» (e.g. «تأكيد الإيداع نعم / تأكيد السحب
     لا» although the role key store.review grants both). Sparse storage:
     «حسب الدور» deletes the override. The role page splits derived actions the
     same way.
  2. A new admin without a role gets the least-privileged role — never «مدير عام»
     (repo, web, API, sub-manager).
  3. «مدير عام» sees and manages ALL distributors (web + API); a limited manager
     only his own; a distributor login never widens.

One app per module; every test creates its own admins/roles/distributors.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from uuid import uuid4

import pytest
from werkzeug.datastructures import MultiDict

PW = "Pass-12345"


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_tristate_")
    old = {k: os.environ.get(k) for k in ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER",
                                          "HOBERADIUS_NO_SEED",
                                          "HOBERADIUS_LICENSE_GATE_TEST_BYPASS")}
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
        sa = admins_repo.get_role_by_name("super_admin")
        admins_repo.create_admin(username="owner_root", password=PW, full_name="Owner",
                                 is_super_admin=True, role_id=sa.id if sa else None)
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
    from app.radius.db.repos import admins_repo
    return int(admins_repo.primary_admin_id())


def _role(perms, granular=None):
    from app.radius.db.repos import admins_repo
    r = admins_repo.create_role(name="r_" + uuid4().hex[:8], display_name="R",
                                permissions=tuple(perms))
    if granular is not None:
        admins_repo.set_role_granular(r.id, granular)
    return r


def _super_role_id() -> int:
    from app.radius.db.repos import admins_repo
    return int(admins_repo.get_role_by_name("super_admin").id)


def _admin(role_id, *, name=None):
    from app.radius.db.repos import admins_repo
    return admins_repo.create_admin(username=name or ("m_" + uuid4().hex[:8]), password=PW,
                                    full_name="M", is_super_admin=False, role_id=role_id)


def _login(client, admin_id):
    from app.radius.auth.session_helpers import _resolve_is_super
    from app.radius.db.repos import admins_repo
    a = admins_repo.get_admin(int(admin_id))
    with client.session_transaction() as s:
        s.clear()
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


def _bearer(admin_id) -> dict:
    from app.radius.db.repos import api_tokens_repo
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name="t-" + uuid4().hex[:6], scopes=["admin:full"],
        created_by=int(admin_id))
    return {"Authorization": "Bearer " + plain}


class _FormGrab(HTMLParser):
    """What the browser would POST for the form with a given data-testid
    (checked radios/checkboxes only; disabled inputs are never sent)."""

    def __init__(self, testid):
        super().__init__()
        self.testid, self.inside, self.fields = testid, False, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("data-testid") == self.testid:
            self.inside = True
            return
        if not self.inside or tag != "input":
            return
        t = (a.get("type") or "text").lower()
        if not a.get("name") or "disabled" in a:
            return
        if t in ("checkbox", "radio"):
            if "checked" in a:
                self.fields.append((a["name"], a.get("value") or "on"))
        elif t not in ("submit", "button", "file"):
            self.fields.append((a["name"], a.get("value") or ""))

    def handle_endtag(self, tag):
        if tag == "form" and self.inside:
            self.inside = False


def _fields(html, testid="operator-policy-form"):
    p = _FormGrab(testid)
    p.feed(html)
    return p.fields


def _with(fields, **over):
    """Replace (or add) single-valued fields; keys given with dots via dict."""
    out = [(k, v) for k, v in fields if k not in over]
    out += list(over.items())
    return MultiDict(out)


def _page(c, mid):
    r = c.get(f"/admin/radius/business-operators/manager/{mid}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _save(c, mid, data):
    r = c.post(f"/admin/radius/business-operators/manager/{mid}/policy", data=data)
    assert r.status_code in (302, 303)
    return r


def _own(mid) -> dict:
    row = _db().execute("SELECT permissions_json, action_grants_json FROM "
                        "manager_distributor_policies WHERE entity_type='manager' "
                        "AND entity_id=?", (mid,)).fetchone()
    if not row:
        return {"flags": {}, "ag": {}}
    return {"flags": json.loads(row["permissions_json"] or "{}"),
            "ag": json.loads(row["action_grants_json"] or "{}")}


def _permitted(app, mid, key):
    with app.test_request_context():
        from app.radius.services import manager_grants as mg
        return mg.action_permitted(mid, key, tenant_id=1)


def _flag(app, mid, flag):
    with app.test_request_context():
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        return ManagerDistributorOpsService(tenant_id=1).has_permission(
            entity_type="manager", entity_id=mid, permission=flag)


def _row_html(html, key):
    m = re.search(r'<div class="tri-row" data-testid="tri-row-%s" data-state="(\w+)">(.*?)'
                  r'data-testid="tri-eff-%s">\s*([^<]+)</span>' % (re.escape(key), re.escape(key)),
                  html, re.S)
    assert m, f"tri row {key} missing"
    return m.group(1), m.group(2), " ".join(m.group(3).split())


_OPER = ["users.view", "users.create", "users.edit", "users.extend", "cards.view",
         "store.review", "online.view", "online.disconnect", "admins.view", "admins.policy"]


# ═══ 1. tri-state manager page — web UI, each of the three states ═════════════
def test_page_renders_a_tri_state_row_for_every_action_and_flag_with_the_role_value(app):
    with app.app_context():
        role = _role(_OPER, {"flags": {"can_see_password": True}})
        mgr = _admin(role.id)
        from app.radius.services import manager_grants as mg
        keys = [r["key"] for r in mg.tri_rows()]
    with app.test_client() as c:
        _login(c, _owner_id())
        html = _page(c, mgr.id)
    for k in keys:
        assert f'data-testid="tri-row-{k}"' in html, k
        for st in ("inherit", "allow", "deny"):
            assert f'data-testid="tri-{k}-{st}"' in html, (k, st)
    # the split halves of one role key are separate rows, both inherited
    state, body, eff = _row_html(html, "store.withdraw_approve")
    assert state == "inherit" and "(الدور: <b>مسموح</b>)" in body and eff == "الفعليّ: مسموح"
    state, body, eff = _row_html(html, "session.force_close")
    assert state == "inherit" and "مسموح" in body and "online.disconnect" in body
    # a flag granted by the role shows the role value too
    state, body, _ = _row_html(html, "can_see_password")
    assert state == "inherit" and "(الدور: <b>مسموح</b>)" in body
    # a derived action whose key the role lacks: «مسموح» disabled + the reason
    state, body, eff = _row_html(html, "storeuser.delete")
    assert "store.user_delete" in body and eff == "الفعليّ: ممنوع"
    assert re.search(r'value="allow"\s+data-testid="tri-storeuser\.delete-allow"\s+'
                     r'(?:checked\s+)?disabled', html)
    # no duplicate names for the old entity checkboxes
    assert 'name="action_edit_offer"' not in html and 'name="action_create_offer"' not in html


def test_saving_each_state_sets_exactly_that_permission_web_and_endpoint(app):
    """«إيداع نعم / سحب لا»: the role key store.review grants both halves; the
    owner switches withdraw alone through the three states from the real page."""
    with app.app_context():
        role = _role(_OPER)
        mgr = _admin(role.id)
    key = "store.withdraw_approve"
    for state, stored, allowed in (("deny", False, False), ("allow", True, True),
                                   ("inherit", None, True), ("deny", False, False)):
        with app.test_client() as c:
            _login(c, _owner_id())
            fields = _fields(_page(c, mgr.id))
            assert ("tri_" + key, "inherit") in fields or ("tri_" + key, "deny") in fields \
                or ("tri_" + key, "allow") in fields
            _save(c, mgr.id, _with(fields, **{"tri_" + key: state}))
            html = _page(c, mgr.id)
        with app.app_context():
            acts = (_own(mgr.id)["ag"].get("_actions") or {})
            assert acts.get(key) is stored, (state, acts)
            assert "store.deposit_approve" not in acts          # untouched half
        assert _permitted(app, mgr.id, key) is allowed, state
        assert _permitted(app, mgr.id, "store.deposit_approve") is True
        st, _body, eff = _row_html(html, key)
        assert st == state and eff == ("الفعليّ: مسموح" if allowed else "الفعليّ: ممنوع")
        # the real endpoints, as the manager: exactly the switched half is refused
        with app.test_client() as m:
            _login(m, mgr.id)
            wd = m.post("/admin/radius/store-support/withdrawals/1/confirm")
            dp = m.post("/admin/radius/store-support/deposits/1/confirm")
            assert (wd.status_code == 403) is (not allowed), (state, wd.status_code)
            assert dp.status_code != 403


def test_disconnect_yes_force_close_no(app):
    with app.app_context():
        mgr = _admin(_role(_OPER).id)
    with app.test_client() as c:
        _login(c, _owner_id())
        _save(c, mgr.id, _with(_fields(_page(c, mgr.id)), **{"tri_session.force_close": "deny"}))
    assert _permitted(app, mgr.id, "session.force_close") is False
    assert _permitted(app, mgr.id, "session.disconnect") is True
    assert _permitted(app, mgr.id, "session.reconcile") is True
    with app.test_client() as m:
        _login(m, mgr.id)
        assert m.post("/admin/radius/online/force-close").status_code == 403
        assert m.post("/admin/radius/online/disconnect").status_code != 403
    # the app sees the same decision
    with app.test_client() as m:
        acts = m.get("/api/admin/me", headers=_bearer(mgr.id)).get_json()["data"]["grants"]["actions"]
        assert acts["session.force_close"] is False and acts["session.disconnect"] is True


def test_flag_and_plain_action_and_entity_gate_three_states(app):
    """A role-granted flag (can_see_password), a plain grant (bulk.ops, role
    default off) and an entity gate (offer.edit, role on) — each state is exact."""
    with app.app_context():
        role = _role(_OPER, {"flags": {"can_see_password": True},
                             "action_grants": {"offer": {"edit": True}}})
        mgr = _admin(role.id)
    cases = (
        ("can_see_password", lambda: _flag(app, mgr.id, "can_see_password"),
         {"inherit": True, "allow": True, "deny": False}),
        ("bulk.ops", lambda: _permitted(app, mgr.id, "bulk.ops"),
         {"inherit": False, "allow": True, "deny": False}),
        ("offer.edit", lambda: _permitted(app, mgr.id, "offer.edit"),
         {"inherit": True, "allow": True, "deny": False}),
    )
    for key, probe, expect in cases:
        for state in ("deny", "allow", "inherit"):
            with app.test_client() as c:
                _login(c, _owner_id())
                _save(c, mgr.id, _with(_fields(_page(c, mgr.id)), **{"tri_" + key: state}))
                st, _b, _e = _row_html(_page(c, mgr.id), key)
            assert st == state, (key, state)
            assert probe() is expect[state], (key, state)
    with app.app_context():
        own = _own(mgr.id)
    assert own["flags"] == {} and own["ag"] == {}, own     # all back to «حسب الدور»


def test_allow_never_exceeds_the_role_key(app):
    with app.app_context():
        mgr = _admin(_role(_OPER).id)                  # no store.user_delete
    with app.test_client() as c:
        _login(c, _owner_id())
        _save(c, mgr.id, _with(_fields(_page(c, mgr.id)), **{"tri_storeuser.delete": "allow"}))
        st, body, eff = _row_html(_page(c, mgr.id), "storeuser.delete")
    assert _permitted(app, mgr.id, "storeuser.delete") is False
    assert st == "allow" and eff == "الفعليّ: ممنوع" and "لا يعمل حتى يُمنح" in body


def test_unchanged_save_with_explicit_overrides_changes_nothing(app):
    with app.app_context():
        mgr = _admin(_role(_OPER, {"flags": {"can_see_balance": True}}).id)
    with app.test_client() as c:
        _login(c, _owner_id())
        _save(c, mgr.id, _with(_fields(_page(c, mgr.id)),
                               **{"tri_store.withdraw_approve": "deny",
                                  "tri_can_see_balance": "deny", "tri_bulk.ops": "allow"}))
    with app.app_context():
        before = _own(mgr.id)
    with app.test_client() as c:
        _login(c, _owner_id())
        _save(c, mgr.id, MultiDict(_fields(_page(c, mgr.id))))
    with app.app_context():
        after = _own(mgr.id)
    assert before == after
    assert after["ag"]["_actions"] == {"store.withdraw_approve": False, "bulk.ops": True}
    assert after["flags"] == {"can_see_balance": False}


def test_saving_one_override_does_not_copy_role_grants_into_the_manager_row(app):
    """The grant setters used to write the ROLE-MERGED view back to the manager's
    row — a later change to the role then never reached him."""
    with app.app_context():
        role = _role(_OPER, {"action_grants": {"_actions": {"bulk.ops": True},
                                               "offer": {"edit": True}}})
        mgr = _admin(role.id)
        from app.radius.services import manager_grants as mg
        mg.set_action_override(mgr.id, "session.force_close", False, tenant_id=1)
        mg.set_action_grants(mgr.id, "batch", None, tenant_id=1)
        mg.set_field_grants(mgr.id, "subscriber", None, tenant_id=1)
        own = _own(mgr.id)
        assert own["ag"] == {"_actions": {"session.force_close": False}}, own
        from app.radius.db.repos import admins_repo
        admins_repo.set_role_granular(role.id, {})
    assert _permitted(app, mgr.id, "bulk.ops") is False       # follows the role now
    assert _permitted(app, mgr.id, "offer.edit") is False


# ═══ role page — the same split for derived actions ═══════════════════════════
def _role_grants_fields(c, role_id):
    html = c.get(f"/admin/radius/roles/{role_id}/edit").get_data(as_text=True)
    m = re.search(r'<form[^>]+action="([^"]*/roles/%d/grants)"' % role_id, html)
    assert m
    start = html.find(m.group(0))
    chunk = html[start:html.find("</form>", start)]
    chunk = chunk.replace(m.group(0), m.group(0).replace("<form", '<form data-testid="g"'))
    return m.group(1), _fields(chunk + "</form>", "g"), html


def test_role_page_splits_a_role_key_for_all_its_managers(app):
    with app.app_context():
        role = _role(_OPER)
        a, b = _admin(role.id), _admin(role.id)
    with app.test_client() as c:
        _login(c, _owner_id())
        url, fields, html = _role_grants_fields(c, role.id)
        assert 'data-testid="role-tri-row-store.withdraw_approve"' in html
        assert ("tri_store.withdraw_approve", "inherit") in fields
        r = c.post(url, data=_with(fields, **{"tri_store.withdraw_approve": "deny"}))
        assert r.status_code in (302, 303)
    for m in (a, b):
        assert _permitted(app, m.id, "store.withdraw_approve") is False
        assert _permitted(app, m.id, "store.deposit_approve") is True
    # the manager page shows the role's split, and one manager can be re-allowed
    with app.test_client() as c:
        _login(c, _owner_id())
        st, body, _e = _row_html(_page(c, a.id), "store.withdraw_approve")
        assert st == "inherit" and "مُطفأ في أساس الدور" in body
        _save(c, a.id, _with(_fields(_page(c, a.id)), **{"tri_store.withdraw_approve": "allow"}))
    assert _permitted(app, a.id, "store.withdraw_approve") is True
    assert _permitted(app, b.id, "store.withdraw_approve") is False
    # an old client posting the role form without the tri fields keeps the split
    with app.test_client() as c:
        _login(c, _owner_id())
        url, fields, _h = _role_grants_fields(c, role.id)
        legacy = MultiDict([(k, v) for k, v in fields if not k.startswith("tri_")])
        c.post(url, data=legacy)
    assert _permitted(app, b.id, "store.withdraw_approve") is False
    # «حسب المفتاح» removes it again
    with app.test_client() as c:
        _login(c, _owner_id())
        url, fields, _h = _role_grants_fields(c, role.id)
        c.post(url, data=_with(fields, **{"tri_store.withdraw_approve": "inherit"}))
    assert _permitted(app, b.id, "store.withdraw_approve") is True


def test_super_role_page_has_no_split_but_managers_can_still_be_denied(app):
    with app.app_context():
        mgr = _admin(_super_role_id())
    with app.test_client() as c:
        _login(c, _owner_id())
        html = c.get(f"/admin/radius/roles/{_super_role_id()}/edit").get_data(as_text=True)
        assert "role-tri-row-" not in html
        _save(c, mgr.id, _with(_fields(_page(c, mgr.id)), **{"tri_store.withdraw_approve": "deny"}))
    assert _permitted(app, mgr.id, "store.withdraw_approve") is False
    assert _permitted(app, mgr.id, "store.deposit_approve") is True


# ═══ non-owner editors cannot escalate through the page ═══════════════════════
def test_non_owner_editor_cannot_self_edit_or_grant_what_he_lacks(app):
    with app.app_context():
        editor = _admin(_role(_OPER).id)               # admins.policy, no can_see_password
        target = _admin(_role(_OPER).id)
    with app.test_client() as c:
        _login(c, editor.id)
        _save(c, target.id, _with(_fields(_page(c, target.id)),
                                  **{"tri_can_see_password": "allow",
                                     "tri_store.withdraw_approve": "deny"}))
        with app.app_context():
            own = _own(target.id)
        assert "can_see_password" not in own["flags"]                   # refused
        assert own["ag"]["_actions"] == {"store.withdraw_approve": False}  # a deny is fine
        # his own page: refused entirely
        _save(c, editor.id, MultiDict({"tri_can_see_password": "allow"}))
        with app.app_context():
            assert "can_see_password" not in _own(editor.id)["flags"]
        # the owner's page: refused
        _save(c, _owner_id(), MultiDict({"tri_bulk.ops": "deny"}))
        with app.app_context():
            assert "bulk.ops" not in json.dumps(_own(_owner_id()))


# ═══ migration 183 keeps deliberate overrides the page can now express ═══════
def test_migration_183_keeps_deliberate_session_store_overrides(app):
    with app.app_context():
        mgr = _admin(_role(_OPER).id)
        _db().execute(
            "INSERT INTO manager_distributor_policies(tenant_id, entity_type, entity_id,"
            " permissions_json, limits_json, action_grants_json, status, created_at, updated_at)"
            " VALUES(1,'manager',?,?,?,?,'active',datetime('now'),datetime('now'))",
            (mgr.id, json.dumps({"can_see_profit": True}), "{}",
             json.dumps({"_actions": {"store.withdraw_approve": False,
                                      "session.force_close": False,
                                      "storeuser.create": True,
                                      "subscriber.extend": False}})))
        sql = io.open(os.path.join(os.path.dirname(__file__), "..", "app", "radius", "db",
                                   "migrations", "183_repair_grants_d01_d02.sql"),
                      encoding="utf-8").read()
        _db().executescript(sql)
        acts = _own(mgr.id)["ag"]["_actions"]
        assert acts == {"store.withdraw_approve": False, "session.force_close": False,
                        "storeuser.create": True}, acts
    assert _permitted(app, mgr.id, "store.withdraw_approve") is False
    assert _permitted(app, mgr.id, "store.deposit_approve") is True
    assert _permitted(app, mgr.id, "subscriber.extend") is True     # corrupt False removed


# ═══ 2. least-privileged default for a new admin without a role ═══════════════
def _role_name_of(admin_id):
    from app.radius.db.repos import admins_repo
    a = admins_repo.get_admin(int(admin_id))
    r = admins_repo.get_role(int(a.role_id)) if a and a.role_id else None
    return r.name if r else None


def test_repo_default_is_least_privileged_never_super(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        a = admins_repo.create_admin(username="nr_" + uuid4().hex[:6], password=PW)
        assert a.role_id == admins_repo.least_privileged_role_id()
        assert _role_name_of(a.id) == "viewer"
        # even the bare flag does not buy «مدير عام»
        b = admins_repo.create_admin(username="nr_" + uuid4().hex[:6], password=PW,
                                     is_super_admin=True)
        assert _role_name_of(b.id) != "super_admin"


def test_web_create_without_role_is_viewer_and_unknown_role_is_422(app):
    with app.test_client() as c:
        _login(c, _owner_id())
        u = "wa_" + uuid4().hex[:6]
        r = c.post("/admin/radius/admins", data={"username": u, "password": PW,
                                                  "full_name": "W", "enabled": "1"})
        assert r.status_code in (200, 302, 303)
        with app.app_context():
            from app.radius.db.repos import admins_repo
            a = admins_repo.get_by_username(u)
            assert a is not None and _role_name_of(a.id) == "viewer"
        u2 = "wa_" + uuid4().hex[:6]
        r = c.post("/admin/radius/admins", data={"username": u2, "password": PW,
                                                  "role_id": "987654", "enabled": "1"})
        assert r.status_code == 422
        with app.app_context():
            from app.radius.db.repos import admins_repo
            assert admins_repo.get_by_username(u2) is None


def test_api_create_without_role_is_viewer(app):
    with app.test_client() as c:
        u = "ap_" + uuid4().hex[:6]
        r = c.post("/api/v1/admins", json={"username": u, "password": PW},
                   headers=_bearer(_owner_id()))
        assert r.status_code == 201, r.get_data(as_text=True)
    with app.app_context():
        from app.radius.db.repos import admins_repo
        assert _role_name_of(admins_repo.get_by_username(u).id) == "viewer"


def test_sub_manager_of_a_super_role_parent_is_not_super(app):
    with app.app_context():
        parent = _admin(_super_role_id())             # «مدير عام», not the owner
        plain = _role(["users.view"])
    with app.test_client() as c:
        _login(c, parent.id)
        u = "sub_" + uuid4().hex[:6]
        c.post("/admin/radius/business-operators/sub-managers",
               data={"username": u, "password": PW})
        with app.app_context():
            from app.radius.db.repos import admins_repo
            child = admins_repo.get_by_username(u)
            assert child is not None and _role_name_of(child.id) == "viewer"
        # he cannot hand out «مدير عام» explicitly either
        u2 = "sub_" + uuid4().hex[:6]
        c.post("/admin/radius/business-operators/sub-managers",
               data={"username": u2, "password": PW, "role_id": str(_super_role_id())})
        with app.app_context():
            assert admins_repo.get_by_username(u2) is None
        # an explicit role within his own permissions is used
        u3 = "sub_" + uuid4().hex[:6]
        c.post("/admin/radius/business-operators/sub-managers",
               data={"username": u3, "password": PW, "role_id": str(plain.id)})
        with app.app_context():
            assert admins_repo.get_by_username(u3).role_id == plain.id


def test_license_sync_non_owner_never_falls_back_to_super(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from werkzeug.security import generate_password_hash
        _db().execute("UPDATE roles SET name=name||'_x' WHERE name='support'")
        try:
            a = admins_repo.upsert_license_admin_user(
                external_user_id="ext-" + uuid4().hex[:6], username="lic_" + uuid4().hex[:6],
                password_hash=generate_password_hash("x"), role_key="support")
            assert _role_name_of(a.id) != "super_admin"
        finally:
            _db().execute("UPDATE roles SET name='support' WHERE name='support_x'")


# ═══ 3. «مدير عام» sees and manages ALL distributors ══════════════════════════
def _dist(owner_id, *, login_admin_id=None):
    from app.radius.db.repos import operations_repo
    name = "d_" + uuid4().hex[:8]
    return operations_repo.create_distributor(1, {
        "admin_id": owner_id, "login_admin_id": login_admin_id,
        "name": name, "display_name": name, "status": "active",
        "permissions": [], "scope": {}}, actor="t")


_FIN = ["reports.view", "reports.finance", "cards.view", "users.view"]


def test_super_role_sees_and_manages_all_distributors_web_and_api(app):
    with app.app_context():
        lim_role = _role(_FIN, {"flags": {"can_manage_distributors": True}})
        a, b = _admin(lim_role.id), _admin(lim_role.id)
        sup = _admin(_super_role_id())
        da, db_ = _dist(a.id), _dist(b.id)
    # «مدير عام»: every distributor, web and API; editing keeps the owner
    with app.test_client() as c:
        _login(c, sup.id)
        html = c.get("/admin/radius/distributors").get_data(as_text=True)
        assert da["name"] in html and db_["name"] in html
        assert c.get(f"/admin/radius/distributors/{db_['id']}").status_code == 200
        assert c.get(f"/admin/radius/distributors/{db_['id']}/edit").status_code == 200
        r = c.post(f"/admin/radius/distributors/{db_['id']}/edit",
                   data={"name": db_["name"], "display_name": db_["name"] + "_2", "status": "active",
                         "admin_id": str(b.id)})
        assert r.status_code in (302, 303)
        api = c.get("/api/v1/distributors", headers=_bearer(sup.id))
        names = {i["name"] for i in api.get_json()["data"]["items"]}
        assert {da["name"], db_["name"]} <= names
        assert c.get(f"/api/v1/distributors/{db_['id']}/summary",
                     headers=_bearer(sup.id)).status_code == 200
    with app.app_context():
        from app.radius.db.repos import operations_repo
        after = operations_repo.get_distributor(1, db_["id"])
        assert int(after["admin_id"]) == b.id and after["display_name"] == db_["name"] + "_2"
    # a limited manager: only his own — web and API (list, detail, actions)
    with app.test_client() as c:
        _login(c, a.id)
        html = c.get("/admin/radius/distributors").get_data(as_text=True)
        assert da["name"] in html and db_["name"] not in html
        assert c.get(f"/admin/radius/distributors/{db_['id']}").status_code == 403
        api = c.get("/api/v1/distributors", headers=_bearer(a.id))
        names = {i["name"] for i in api.get_json()["data"]["items"]}
        assert da["name"] in names and db_["name"] not in names
        for path, method in ((f"/api/v1/distributors/{db_['id']}/summary", "get"),
                             (f"/api/v1/distributors/{db_['id']}/batches", "get"),
                             (f"/api/v1/distributors/{db_['id']}/settle", "post"),
                             (f"/api/v1/distributors/{db_['id']}/assign-batch", "post")):
            r = getattr(c, method)(path, headers=_bearer(a.id),
                                   **({"json": {"amount": 1}} if method == "post" else {}))
            assert r.status_code == 403, (path, r.status_code)
        assert c.get(f"/api/v1/distributors/{da['id']}/summary",
                     headers=_bearer(a.id)).status_code == 200
        # API create: always owned by the creator (web parity)
        r = c.post("/api/v1/distributors", headers=_bearer(a.id),
                   json={"name": "d_" + uuid4().hex[:8], "admin_id": b.id})
        assert r.status_code == 201
        assert int(r.get_json()["data"]["distributor"]["admin_id"]) == a.id


def test_distributor_login_with_super_role_stays_scoped(app):
    with app.app_context():
        lim = _admin(_role(_FIN).id)
        login = _admin(_super_role_id())
        mine = _dist(lim.id, login_admin_id=login.id)
        other = _dist(lim.id)
        from app.radius.services.distributor_scope import sees_all_distributors
        assert sees_all_distributors(login.id, tenant_id=1) is False
    with app.test_client() as c:
        _login(c, login.id)
        html = c.get("/admin/radius/distributors").get_data(as_text=True)
        assert other["name"] not in html
        api = c.get("/api/v1/distributors", headers=_bearer(login.id))
        names = {i["name"] for i in (api.get_json().get("data") or {}).get("items") or []}
        assert other["name"] not in names
        assert c.get(f"/api/v1/distributors/{other['id']}/summary",
                     headers=_bearer(login.id)).status_code == 403
        # it can still read its own record, but never act on it
        assert c.post(f"/api/v1/distributors/{mine['id']}/settle", json={"amount": 1},
                      headers=_bearer(login.id)).status_code == 403


def test_business_operators_distributor_list_is_scoped(app):
    with app.app_context():
        role = _role(_FIN + ["admins.view"])
        a, b = _admin(role.id), _admin(role.id)
        sup = _admin(_super_role_id())
        da, db_ = _dist(a.id), _dist(b.id)
    with app.test_client() as c:
        _login(c, a.id)
        html = c.get("/admin/radius/business-operators").get_data(as_text=True)
        assert da["name"] in html and db_["name"] not in html
        assert c.get(f"/admin/radius/business-operators/distributor/{db_['id']}").status_code == 403
        _login(c, sup.id)
        html = c.get("/admin/radius/business-operators").get_data(as_text=True)
        assert da["name"] in html and db_["name"] in html
        assert c.get(f"/admin/radius/business-operators/distributor/{db_['id']}").status_code == 200
