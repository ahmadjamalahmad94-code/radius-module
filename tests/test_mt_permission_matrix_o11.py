"""O11 — Permission review matrix."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o11_")
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


def _login(client, *, super_admin=True):
    from app.radius.db.repos import admins_repo
    u = f"o11_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o11-pass", full_name="O11",
        is_super_admin=super_admin,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o11-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    return u


# ─── Service (pure) ─────────────────────────────────────────


def test_empty_db_matrix_lists_no_rows(app):
    with app.app_context():
        from app.radius.services.mt_permission_matrix import build_matrix
        m = build_matrix()
    # ALL_PERMISSIONS is a known small set.
    from app.radius.services import mt_permissions as mp
    assert set(m.permissions) == set(mp.ALL_PERMISSIONS)
    assert m.rows == ()
    assert m.total_admins() == 0


def test_super_admin_row_has_all_cells_true(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        admins_repo.create_admin(
            username="alice_o11", password="pw",
            full_name="Alice", is_super_admin=True,
        )
        from app.radius.services.mt_permission_matrix import build_matrix
        m = build_matrix()
    rows = [r for r in m.rows if r.username == "alice_o11"]
    assert rows, "alice_o11 missing from matrix"
    r = rows[0]
    assert r.is_super_admin is True
    assert r.via_super is True
    assert all(r.granted.values())
    assert r.granted_count == len(m.permissions)


def test_non_super_with_role_only_has_listed_perms(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services import mt_permissions as mp
        admins_repo.ensure_default_roles()
        # The FIRST admin is the primary owner (smallest id = uncapped/super by
        # invariant). Seed one so `bob` below is a genuine NON-owner subject —
        # otherwise bob would be the min-id owner and resolve as via_super.
        admins_repo.create_admin(
            username=f"owner_{uuid4().hex[:6]}", password="pw",
            full_name="Owner", is_super_admin=True,
        )
        # Create a role that only grants PERM_VIEW and PERM_BACKUP.
        role = admins_repo.create_role(
            name=f"viewer_{uuid4().hex[:6]}",
            display_name="O11 viewer",
            permissions=(mp.PERM_VIEW, mp.PERM_BACKUP),
        )
        admins_repo.create_admin(
            username="bob_o11", password="pw",
            full_name="Bob",
            role_id=role.id, is_super_admin=False,
        )
        from app.radius.services.mt_permission_matrix import build_matrix
        m = build_matrix()
    r = [r for r in m.rows if r.username == "bob_o11"][0]
    assert r.is_super_admin is False
    assert r.via_super is False
    assert r.via_admin is False
    assert r.granted[mp.PERM_VIEW] is True
    assert r.granted[mp.PERM_BACKUP] is True
    assert r.granted[mp.PERM_PROGRAM] is False
    assert r.granted[mp.PERM_ROLLBACK] is False
    assert r.granted_count == 2


def test_mikrotik_admin_perm_implies_subordinates(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services import mt_permissions as mp
        admins_repo.ensure_default_roles()
        # Seed the primary owner first so `carol` is a NON-owner subject.
        admins_repo.create_admin(
            username=f"owner_{uuid4().hex[:6]}", password="pw",
            full_name="Owner", is_super_admin=True,
        )
        role = admins_repo.create_role(
            name=f"mtadmin_{uuid4().hex[:6]}",
            display_name="MT admin role",
            permissions=(mp.PERM_ADMIN,),
        )
        admins_repo.create_admin(
            username="carol_o11", password="pw",
            full_name="Carol",
            role_id=role.id, is_super_admin=False,
        )
        from app.radius.services.mt_permission_matrix import build_matrix
        m = build_matrix()
    r = [r for r in m.rows if r.username == "carol_o11"][0]
    assert r.via_admin is True
    assert r.via_super is False
    # Everything PERM_ADMIN implies is granted.
    for p in (mp.PERM_VIEW, mp.PERM_PROGRAM, mp.PERM_BACKUP,
              mp.PERM_RESTORE, mp.PERM_AUDIT_VIEW):
        assert r.granted[p] is True


def test_grants_for_counts_per_column(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services import mt_permissions as mp
        admins_repo.ensure_default_roles()
        # Seed the primary owner first so neither role member is the min-id owner
        # (the owner resolves as via_super = every perm).
        admins_repo.create_admin(
            username=f"owner_{uuid4().hex[:6]}", password="pw",
            full_name="Owner", is_super_admin=True,
        )
        role = admins_repo.create_role(
            name=f"r_{uuid4().hex[:6]}",
            display_name="role",
            permissions=(mp.PERM_VIEW,),
        )
        u1 = f"u1_{uuid4().hex[:4]}"
        u2 = f"u2_{uuid4().hex[:4]}"
        admins_repo.create_admin(
            username=u1, password="pw", full_name="u1", role_id=role.id,
        )
        admins_repo.create_admin(
            username=u2, password="pw", full_name="u2", role_id=role.id,
        )
        from app.radius.services.mt_permission_matrix import build_matrix
        m = build_matrix()
    # Both role members grant PERM_VIEW.
    assert m.grants_for(mp.PERM_VIEW) >= 2
    # Neither role member (NON-owner) is granted PERM_PROGRAM. (The seeded
    # primary owner is via_super and intentionally holds every column.)
    members = [r for r in m.rows if r.username in {u1, u2}]
    assert members and all(not r.granted.get(mp.PERM_PROGRAM) for r in members)


# ─── Route ──────────────────────────────────────────────────


def test_permission_matrix_route_login_guarded(client):
    res = client.get("/admin/radius/permissions",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_permission_matrix_route_renders_table(app, client):
    _login(client, super_admin=True)
    res = client.get("/admin/radius/permissions")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-permission-matrix" in html
    assert "data-mt-perm-table" in html


def test_permission_matrix_route_lists_logged_in_admin(app, client):
    u = _login(client, super_admin=True)
    html = client.get(
        "/admin/radius/permissions").get_data(as_text=True)
    assert u in html
    # A super admin row should appear with the super-admin marker.
    assert 'data-mt-perm-super="1"' in html


def test_permission_matrix_route_shows_perm_columns(app, client):
    _login(client, super_admin=True)
    html = client.get(
        "/admin/radius/permissions").get_data(as_text=True)
    from app.radius.services import mt_permissions as mp
    # Every permission code appears as a column attribute.
    for p in mp.ALL_PERMISSIONS:
        assert f'data-mt-perm-col="{p}"' in html


def test_permission_matrix_route_renders_grant_count(app, client):
    _login(client, super_admin=True)
    html = client.get(
        "/admin/radius/permissions").get_data(as_text=True)
    # Footer summary cell appears once per permission.
    from app.radius.services import mt_permissions as mp
    for p in mp.ALL_PERMISSIONS:
        assert f'data-mt-perm-col-count="{p}"' in html
