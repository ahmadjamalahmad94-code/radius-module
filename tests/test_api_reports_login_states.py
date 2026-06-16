"""feat/api-first-parity — reports login-states JSON (group 8).

يعكس صفحات rep_login_states_detail عبر login_events نفسه: نظرة عامة +
تفصيل لكل قسم من الخمسة (مع تثبيت source). شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_loginstates_api_")
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


def _seed_radpostauth(app):
    """صفّ Access-Reject لمشترك (RADIUS) كي يظهر في قسم المشتركين."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            conn.execute(
                "INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas) "
                "VALUES (1,'sub-x','***','Access-Reject',?,'password_wrong','10.0.0.1')", (now,))


def test_requires_auth(client):
    assert client.get("/api/v1/reports/login-states").status_code == 401


def test_overview(client):
    res = client.get("/api/v1/reports/login-states", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert "overview" in data
    assert set(data["kinds"]) == {"subscribers", "cards", "sub_portal", "card_store", "admin"}


def test_detail_shape_all_kinds(client):
    for kind in ("subscribers", "cards", "sub_portal", "card_store", "admin"):
        res = client.get(f"/api/v1/reports/login-states/{kind}", headers=AUTH)
        assert res.status_code == 200, (kind, res.get_json())
        d = res.get_json()["data"]
        assert d["kind"] == kind
        for key in ("rows", "stats", "shown", "matched", "source_locked", "actor"):
            assert key in d


def test_detail_unknown_kind_404(client):
    res = client.get("/api/v1/reports/login-states/bogus", headers=AUTH)
    assert res.status_code == 404
    assert "kinds" in res.get_json()["error"]["details"]


def test_source_lock_enforced(client):
    # المشتركون مثبّتون على network — تمرير source=portal لا يتجاوز القفل
    res = client.get("/api/v1/reports/login-states/subscribers?source=portal", headers=AUTH)
    assert res.status_code == 200
    assert res.get_json()["data"]["source_locked"] is True
    # admin غير مثبّت
    adm = client.get("/api/v1/reports/login-states/admin", headers=AUTH)
    assert adm.get_json()["data"]["source_locked"] is False


def test_detail_returns_seeded_radius_reject(app, client):
    _seed_radpostauth(app)
    res = client.get("/api/v1/reports/login-states/subscribers", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    rows = res.get_json()["data"]["rows"]
    assert any((r.get("username") == "sub-x") for r in rows), rows
