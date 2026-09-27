"""SEC H1 (follow-up) — the recycle bin must not bypass the super-only gate.

c754d03c gated /api/v1/admins* and role mutations to super admins / the
primary owner. But the recycle-bin API still let ANY authenticated principal
POST /api/v1/recycle-bin/{admin|role}/<id>/{archive|restore}, which
soft-deletes (disables) an admin — including a super admin — or resurrects an
archived one. That is account management through a side door.

Contract pinned here:
  * a non-super admin (HTTP Basic) gets 403 `forbidden` on archive AND restore
    for admins and roles;
  * the same principal is still allowed on non-privileged entity types
    (the gate is scoped, not a blanket lock);
  * a super admin keeps working end-to-end (archive → restore);
  * the web restore route refuses admins/roles for a non-super session.
"""
from __future__ import annotations

import sys
import time

import pytest

for _m in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
    sys.modules.pop(_m, None)


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_admin(app, *, super_admin: bool = False):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.core.tenant import TenantMembership, DEFAULT_TENANT_ID
        from app.radius.stores.tenants_store import TenantsStore
        u = f"rb_{'sup' if super_admin else 'low'}_{int(time.time() * 1000)}"
        admin = admins_repo.create_admin(
            username=u, password="pw-1234", full_name="RB gate",
            is_super_admin=super_admin, enabled=True,
        )
        TenantsStore.instance().add_membership(TenantMembership(
            id=None, tenant_id=DEFAULT_TENANT_ID, admin_id=admin.id,
            role_id=admin.role_id, status="active"))
        return admin.id, u, "pw-1234"


def _make_role(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        role = admins_repo.create_role(
            name=f"rb_role_{int(time.time() * 1000)}",
            display_name="RB role", permissions=[])
        return role.id


# ─── non-super is refused on both verbs, for both privileged types ───

@pytest.mark.parametrize("etype", ["admin", "admins", "role", "roles"])
@pytest.mark.parametrize("verb", ["archive", "restore"])
def test_non_super_cannot_touch_admins_or_roles(app, client, etype, verb):
    _, u, p = _make_admin(app)
    res = client.post(f"/api/v1/recycle-bin/{etype}/1/{verb}", auth=(u, p))
    assert res.status_code == 403, res.get_json()
    assert res.get_json()["error"]["code"] == "forbidden"


def test_non_super_cannot_archive_a_super_admin(app, client):
    """The concrete attack: a low-priv token disables the owner's super account."""
    target_id, _, _ = _make_admin(app, super_admin=True)
    _, u, p = _make_admin(app)
    res = client.post(f"/api/v1/recycle-bin/admin/{target_id}/archive",
                      json={"reason": "bypass"}, auth=(u, p))
    assert res.status_code == 403
    with app.app_context():
        from app.radius.db.repos import admins_repo
        assert admins_repo.get_admin(target_id) is not None, "target must stay live"


# ─── the gate is scoped: other entity types keep the old behaviour ───

def test_non_super_still_reaches_non_privileged_types(app, client):
    _, u, p = _make_admin(app)
    res = client.post("/api/v1/recycle-bin/subscriber/999999999/restore", auth=(u, p))
    # not_found (or an RBAC scope 403 from elsewhere) is fine — but it must NOT
    # be the admin-management refusal.
    body = res.get_json() or {}
    assert not (res.status_code == 403 and "super" in (body.get("error") or {}).get("message", ""))


# ─── a super admin keeps the full round trip ───

def test_super_admin_can_archive_and_restore_admin_and_role(app, client):
    # A super admin created here (not the bootstrap owner — its password
    # differs between a fresh DB and the dev DB) exercises the allow path.
    _, su, sp = _make_admin(app, super_admin=True)
    target_id, _, _ = _make_admin(app)
    role_id = _make_role(app)

    res = client.post(f"/api/v1/recycle-bin/admin/{target_id}/archive",
                      json={"reason": "test"}, auth=(su, sp))
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["archived"] is True
    res = client.post(f"/api/v1/recycle-bin/admin/{target_id}/restore", auth=(su, sp))
    assert res.status_code == 200, res.get_json()

    res = client.post(f"/api/v1/recycle-bin/role/{role_id}/archive",
                      json={"reason": "test"}, auth=(su, sp))
    assert res.status_code == 200, res.get_json()
    res = client.post(f"/api/v1/recycle-bin/role/{role_id}/restore", auth=(su, sp))
    assert res.status_code == 200, res.get_json()


# ─── web parity: /admin/radius/recycle-bin/<admins|roles>/<id>/restore ───

def test_web_restore_of_admin_refused_for_non_super_session(app, client):
    target_id, _, _ = _make_admin(app)
    with client.session_transaction() as sess:
        sess["admin_id"] = 2
        sess["is_super_admin"] = False
        sess["permissions"] = ["cards.restore"]
        sess["tenant_id"] = 1
    res = client.post(f"/admin/radius/recycle-bin/admins/{target_id}/restore",
                      follow_redirects=False)
    assert res.status_code in (302, 401, 403)
    assert res.status_code != 200
    # whatever the session layer decides (login redirect / RBAC), the admin
    # row must not have been touched
    with app.app_context():
        from app.radius.db.repos import admins_repo
        assert admins_repo.get_admin(target_id) is not None
