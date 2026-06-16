"""feat/api-first-parity — mikrotik permission-matrix JSON (group 7f).

يعكس صفحة /admin/radius/permissions عبر build_matrix. شغّل وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mtpm_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_admin(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            conn.execute("INSERT INTO admins(username, password_hash, full_name, is_super_admin, enabled, created_at) "
                         "VALUES ('boss','x','Boss',1,1,?)", (now,))


def test_requires_auth(client):
    assert client.get("/api/v1/mikrotik/permissions").status_code == 401


def test_matrix_shape(app, client):
    _seed_admin(app)
    res = client.get("/api/v1/mikrotik/permissions", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    for key in ("permissions", "perm_labels", "rows", "grant_counts",
                "total_admins", "group_cards", "summary", "risky_perms"):
        assert key in d
    assert isinstance(d["permissions"], list) and len(d["permissions"]) >= 1
    # المدير المثبّت موجود كصفّ سوبر
    assert any(r["username"] == "boss" and r["is_super_admin"] for r in d["rows"])
    assert d["summary"]["super_admins"] >= 1


def test_group_cards_present(app, client):
    _seed_admin(app)
    d = client.get("/api/v1/mikrotik/permissions", headers=AUTH).get_json()["data"]
    assert isinstance(d["group_cards"], list) and len(d["group_cards"]) >= 1
    assert all("permission_count" in c for c in d["group_cards"])
