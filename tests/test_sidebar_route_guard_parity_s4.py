"""S4 — Sidebar/route guard parity (Broken Access Control fix).

Before this fix the admin sidebar hid sections by RBAC, but the *pages*
themselves were not guarded server-side: a non-permitted admin could reach
a "hidden" view simply by typing its URL. These tests pin the fix:

  * A permissionless (non-super) admin gets HTTP 403 on a guarded view
    route via direct URL — both a super-only area (/roles) and a plain
    view-perm area (/cards/overview).
  * A super-admin gets HTTP 200 on the same routes (not over-blocked).
  * The primary admin (lowest id = owner) ALWAYS resolves as super even
    when its is_super_admin flag is False — «المدير الرئيسي = وصول كامل
    دائماً».
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s4_guard_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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


@pytest.fixture
def client(app):
    return app.test_client()


def _make_admin(*, is_super_admin: bool, viewer: bool = False):
    """Create an admin. viewer=True → only dashboard.view (no view perms);
    otherwise role_id defaults to super_admin role (all RBAC perms)."""
    from app.radius.db.repos import admins_repo
    role_id = None
    if viewer:
        r = admins_repo.get_role_by_name("viewer")
        role_id = r.id if r else None
    return admins_repo.create_admin(
        username=f"s4_{uuid4().hex[:8]}",
        password="s4-pass",
        full_name="S4 Tester",
        role_id=role_id,
        is_super_admin=is_super_admin,
    )


def _login(client, username: str):
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "s4-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}, res.status_code
    return res


# ─── (a) server-side enforcement: permissionless admin is 403 ──────────


def test_permissionless_admin_403_on_super_only_route(app, client):
    # owner first (becomes primary/super), then a permissionless viewer.
    owner = _make_admin(is_super_admin=True)
    limited = _make_admin(is_super_admin=False, viewer=True)
    _login(client, limited.username)
    # /roles is super-admin-only in the sidebar perm map.
    res = client.get("/admin/radius/roles", follow_redirects=False)
    assert res.status_code == 403
    assert "Location" not in res.headers  # 403, not a login redirect


def test_permissionless_admin_403_on_view_perm_route(app, client):
    _make_admin(is_super_admin=True)  # primary/owner occupies id #1
    limited = _make_admin(is_super_admin=False, viewer=True)
    _login(client, limited.username)
    # /cards/overview needs cards.view — a viewer lacks it.
    res = client.get("/admin/radius/cards/overview", follow_redirects=False)
    assert res.status_code == 403


# ─── super-admin keeps access (not over-blocked) ───────────────────────


def test_super_admin_200_on_guarded_routes(app, client):
    owner = _make_admin(is_super_admin=True)
    _login(client, owner.username)
    assert client.get("/admin/radius/roles").status_code == 200
    assert client.get("/admin/radius/cards/overview").status_code == 200


# ─── (b) primary admin always resolves as super ────────────────────────


def test_primary_admin_resolves_super_even_when_flag_false(app, client):
    # The FIRST admin in the DB is the owner. Even with is_super_admin=False
    # and only the viewer role, it must be treated as super.
    founder = _make_admin(is_super_admin=False, viewer=True)
    from app.radius.db.repos import admins_repo
    assert admins_repo.primary_admin_id() == founder.id
    _login(client, founder.username)
    with client.session_transaction() as sess:
        assert sess.get("is_super_admin") is True
    # …and that translates to real access on a super-only route.
    assert client.get("/admin/radius/roles").status_code == 200


def test_non_primary_with_flag_false_is_not_super(app, client):
    """Sanity: only the *primary* admin gets the implicit super. A later
    non-super admin stays gated."""
    _make_admin(is_super_admin=True)              # primary owner (#1)
    later = _make_admin(is_super_admin=False, viewer=True)  # #2
    _login(client, later.username)
    with client.session_transaction() as sess:
        assert sess.get("is_super_admin") is False
