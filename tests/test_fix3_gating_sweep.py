"""F01-F2 — every button, link and form is gated by the SAME permission as the
request it sends: no visible submit leads to a 403 endpoint.

For every editable RBAC key K, two managers are created — {dashboard.view, K}
and {dashboard.view, <view key of K's area>, K} — and every admin page each of
them may open (plus a few real-id pages) is rendered. Every usable POST form,
``formaction`` button and internal link is resolved to its endpoint and checked
with the server guard's own decision (routes/blueprint.rbac_denial_status). A
«new/edit» link must also lead to a form whose save is allowed. Forms a manager
may view but not save must be rendered read-only (every control disabled).

The engine lives in tests/gating_sweep.py. Run this file on its own.
"""
from __future__ import annotations

import re

import pytest

import gating_sweep as G
import webui_harness as H

_SKIP_WORDS = ("export", ".json", ".csv", ".pdf", ".xlsx", "/api/", "download", "poll",
               "stream", "health", "portal", "logout", "/wz/", "webhook", "collect", "pull",
               "audio", ".rsc", "designer-svg")
_VIEW = {"users": "users.view", "online": "online.view", "cards": "cards.view",
         "plans": "plans.view", "nas": "nas.view", "admins": "admins.view",
         "admin_pricing": "admin_pricing.view", "reports": "reports.view",
         "store": "store.view", "settings": "settings.view", "routers": "routers.view"}


@pytest.fixture(scope="module")
def world():
    app = H.make_app()
    with app.app_context():
        pid = H.plan(name="باقة-المسح")
        u = H.subscriber(pid)
        bid, _ = H.card_batch(pid, 3)
        pages = []
        for rule in app.url_map.iter_rules():
            if (not rule.endpoint.startswith("radius.") or "GET" not in rule.methods
                    or rule.arguments or any(w in rule.rule for w in _SKIP_WORDS)):
                continue
            pages.append((rule.endpoint.split(".", 1)[1], rule.rule))
        pages += [("users_edit", f"/admin/radius/users/{u}/edit"),
                  ("users_profile", f"/admin/radius/users/{u}/profile"),
                  ("users_finance", f"/admin/radius/users/{u}/finance"),
                  ("cards_of_batch", f"/admin/radius/cards/batches/{bid}/cards"),
                  ("plans_edit", f"/admin/radius/plans/{pid}/edit")]
        yield app, pages


def _perm_sets(key):
    sets = [("dashboard.view", key)]
    v = _VIEW.get(key.split(".")[0])
    if v and v != key:
        sets.append(("dashboard.view", v, key))
    return sets


def _sweep(app, pages, perms):
    from app.radius.db.repos import admins_repo
    from app.radius.routes.blueprint import rbac_denial_status
    mgr = H.role_admin(perms)
    # a subscriber of his own, so row-level controls (row menus, entity links)
    # are rendered and swept too
    H.subscriber(None, manager_id=mgr.id)
    eff = list(admins_repo.admin_permissions(admins_repo.get_admin(mgr.id)))
    c = app.test_client()
    H.login_session(c, mgr.id)
    bad = []
    for name, path in pages:
        if rbac_denial_status(name, "GET", is_super=False, perms=eff, admin_id=mgr.id,
                              tenant_id=1, record_activity=False) is not None:
            continue
        r = c.get(path)
        if r.status_code >= 500:
            bad.append(f"{path}: HTTP {r.status_code}")
            continue
        if r.status_code != 200 or "text/html" not in (r.headers.get("Content-Type") or ""):
            continue
        bad += G.violations(app, r.get_data(as_text=True), path, admin=mgr, perms=eff)
    return bad


def _keys():
    import os
    import sys
    sys.path.insert(0, os.getcwd())
    from app.radius.core.constants import EDITABLE_PERMISSIONS
    return [k for k in EDITABLE_PERMISSIONS if k != "dashboard.view"]


@pytest.mark.parametrize("key", _keys())
def test_no_visible_control_leads_to_a_refused_endpoint(world, key):
    app, pages = world
    with app.app_context():
        bad = []
        for perms in _perm_sets(key):
            bad += [f"[{'+'.join(perms[1:])}] {b}" for b in _sweep(app, pages, perms)]
    assert not bad, "\n".join(sorted(set(bad))[:60])


def test_owner_renders_every_page(world):
    app, pages = world
    with app.app_context():
        c = app.test_client()
        H.login_session(c, H.owner_id())
        broken = [f"{p}: {c.get(p).status_code}" for _n, p in pages if c.get(p).status_code >= 500]
    assert not broken, broken


# ─────────── the reported cases, spelled out ───────────

def _page(app, perms, path):
    mgr = H.role_admin(perms)
    c = app.test_client()
    H.login_session(c, mgr.id)
    r = c.get(path)
    return r.status_code, r.get_data(as_text=True)


def test_bandwidth_new_is_hidden_and_readonly_without_plans_edit(world):
    app, _ = world
    with app.app_context():
        st, html = _page(app, ("dashboard.view", "plans.view", "plans.create"), "/admin/radius/bandwidth")
        assert st == 200 and "/admin/radius/bandwidth/new" not in html
        st, html = _page(app, ("dashboard.view", "plans.view", "plans.create"), "/admin/radius/bandwidth/new")
        assert st == 200
        assert 'fieldset disabled class="hr-readonly"' in html and "للعرض فقط" in html
        st, html = _page(app, ("dashboard.view", "plans.view", "plans.create", "plans.edit"),
                         "/admin/radius/bandwidth")
        assert "/admin/radius/bandwidth/new" in html


def test_subscriber_fields_readonly_with_users_view_only(world):
    app, _ = world
    with app.app_context():
        st, html = _page(app, ("dashboard.view", "users.view"), "/admin/radius/subscriber-fields")
        assert st == 200 and 'fieldset disabled class="hr-readonly"' in html
        st, html = _page(app, ("dashboard.view", "users.view", "settings.view", "settings.edit"),
                         "/admin/radius/subscriber-fields")
        assert 'fieldset disabled class="hr-readonly"' not in html


@pytest.mark.parametrize("path", [
    "/admin/radius/settings", "/admin/radius/sms", "/admin/radius/access-control",
    "/admin/radius/anti-mac-clone", "/admin/radius/subscriber-notifications",
    "/admin/radius/integrations",
])
def test_settings_forms_are_readonly_with_settings_view_only(world, path):
    app, _ = world
    with app.app_context():
        mgr = H.role_admin(("dashboard.view", "settings.view"))
        from app.radius.db.repos import admins_repo
        eff = list(admins_repo.admin_permissions(admins_repo.get_admin(mgr.id)))
        c = app.test_client()
        H.login_session(c, mgr.id)
        r = c.get(path)
        if r.status_code != 200:
            pytest.skip(f"{path} not viewable with settings.view on this build ({r.status_code})")
        assert not G.violations(app, r.get_data(as_text=True), path, admin=mgr, perms=eff)


def test_can_submit_matches_the_guard(world):
    """The template helper IS the guard's decision (same function), per key."""
    app, _ = world
    from app.radius.routes.blueprint import rbac_denial_status
    with app.app_context():
        mgr = H.role_admin(("dashboard.view", "plans.view", "plans.create"))
        from app.radius.db.repos import admins_repo
        eff = list(admins_repo.admin_permissions(admins_repo.get_admin(mgr.id)))
        c = app.test_client()
        H.login_session(c, mgr.id)
        c.get("/admin/radius/")
        with app.test_request_context("/admin/radius/"):
            from flask import session
            session.update({"admin_id": mgr.id, "tenant_id": 1, "permissions": eff,
                            "is_super_admin": False})
            ctx = {}
            for proc in app.template_context_processors[None]:
                ctx.update(proc())
            can = ctx["can_submit"]
            for ep in ("plans_create", "plans_update", "plans_delete", "bw_create", "users_create",
                       "settings_page", "online_disconnect"):
                assert can("radius." + ep) == (rbac_denial_status(
                    ep, "POST", is_super=False, perms=eff, admin_id=mgr.id, tenant_id=1,
                    record_activity=False) is None), ep
            assert can("radius.bw_new", "GET") is False          # opens a form it can't save
            assert can("radius.plans_new", "GET") is True


def test_entity_links_keep_their_text_when_unlinked(world):
    """Denied entity links lose only the link, never the data (username/plan)."""
    app, _ = world
    with app.app_context():
        u = H.subscriber(H.plan(name="باقة-ظاهرة"), username="shown_" + H.uuid4().hex[:5])
        st, html = _page(app, ("dashboard.view", "users.view", "scope.view_all_subscribers"),
                         "/admin/radius/users?q=" + u)
        assert st == 200
        assert u in re.sub(r"<[^>]+>", " ", html)
        assert f"/admin/radius/users/{u}/edit" not in html
