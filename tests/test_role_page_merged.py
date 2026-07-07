"""Merged role page (2026-07).

The role «basic info + permissions» editor and the role «actions/visibility/
section-access grants» editor used to be two separate pages
(/roles/<id>/edit and /roles/<id>/grants). They are now ONE comprehensive
page rendered at /roles/<id>/edit:

  * identity + permissions matrix  → POST roles_save
  * inherited actions/visibility/sections → POST roles_grants_save

Both save flows are independent and must still persist, the old /grants URL
must 302 to the merged page, and RBAC (super-admin only) is preserved.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_rolemerge_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _mk_role():
    from app.radius.db.repos import admins_repo, tenants_repo
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    return admins_repo.create_role(name="merge_role", display_name="Merge",
                                   permissions=("store.view",))


def _super(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "tk"


def test_merged_page_renders_both_sections(app):
    with app.app_context():
        role = _mk_role()
    c = app.test_client()
    _super(c)
    res = c.get(f"/admin/radius/roles/{role.id}/edit", follow_redirects=False)
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    # Basic-info + permissions section
    assert "هوية الدور" in body
    assert "الصلاحيات" in body
    assert 'id="role-form"' in body
    # Inherited actions/visibility/section-access section (was /grants page)
    assert "نطاق الرؤية" in body
    assert "الأفعال المسموح بها" in body
    assert 'id="role-grants-form"' in body
    assert 'id="role-grants"' in body


def test_save_basic_info_persists(app):
    with app.app_context():
        role = _mk_role()
    c = app.test_client()
    _super(c)
    res = c.post(f"/admin/radius/roles/{role.id}/save",
                 data={"_csrf_token": "tk",
                       "display_name": "Renamed Role",
                       "description": "new desc",
                       "color": "#123456",
                       "permissions": ["store.view", "cards.view"]},
                 follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import admins_repo
        r = admins_repo.get_role(role.id)
        assert r.display_name == "Renamed Role"
        assert r.description == "new desc"
        assert set(r.permissions) == {"store.view", "cards.view"}


def test_save_grant_persists(app):
    with app.app_context():
        role = _mk_role()
        admin_id = None
        from app.radius.db.repos import admins_repo
        admin = admins_repo.create_admin(username="mgr_merge", password="p",
                                         full_name="M", is_super_admin=False,
                                         role_id=role.id)
        admin_id = admin.id
    c = app.test_client()
    _super(c)
    res = c.post(f"/admin/radius/roles/{role.id}/grants",
                 data={"_csrf_token": "tk",
                       "can_view_all_subscribers": "1",
                       "action_storeuser.create": "1",
                       "section_cards": "hidden"},
                 follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services import manager_grants as mg
        blob = admins_repo.get_role_granular(role.id)
        assert blob.get("flags", {}).get("can_view_all_subscribers") is True
        assert blob.get("section_access", {}).get("cards") == "hidden"
        # A manager with this role inherits the saved grant.
        assert mg.action_permitted(admin_id, "storeuser.create", tenant_id=1) is True


def test_old_grants_url_redirects_to_merged(app):
    with app.app_context():
        role = _mk_role()
    c = app.test_client()
    _super(c)
    res = c.get(f"/admin/radius/roles/{role.id}/grants", follow_redirects=False)
    assert res.status_code == 302
    assert f"/roles/{role.id}/edit" in res.headers.get("Location", "")


def test_merged_page_requires_super_admin(app):
    """RBAC preserved: the merged page shows super-only grants content, so a
    non-super admin is refused (403) — matching the old /grants guard."""
    with app.app_context():
        role = _mk_role()
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = 2
        s["is_super_admin"] = False
        s["tenant_id"] = 1
        s["permissions"] = ["admins.view"]
        s["_csrf_token"] = "tk"
    res = c.get(f"/admin/radius/roles/{role.id}/edit", follow_redirects=False)
    assert res.status_code == 403
