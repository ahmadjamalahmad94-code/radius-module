"""feat/api-first-parity — mikrotik audit-timeline JSON (group 7d).

يعكس صفحة الخط الزمني لراوتر عبر audit_repo.recent + present_many. شغّل وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mtat_api_")
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


def _seed(app, rid=820, with_audit=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        from app.radius.db.repos import audit_repo
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            conn.execute("INSERT INTO nas_devices(id, tenant_id, name, address, secret, vendor, enabled, created_at) "
                         "VALUES (?,1,'MT-AT','10.0.0.8','s','mikrotik',1,?)", (rid, now))
        if with_audit:
            audit_repo.record(tenant_id=1, actor="admin", action="mt.program.apply",
                              target_type="mikrotik_nas", target_id=str(rid),
                              router_id=rid, result_status="success", severity="info")
    return rid


def test_requires_auth(client):
    assert client.get("/api/v1/mikrotik/820/timeline").status_code == 401


def test_unknown_router_404(client):
    assert client.get("/api/v1/mikrotik/9999/timeline", headers=AUTH).status_code == 404


def test_timeline_empty(app, client):
    rid = _seed(app, with_audit=False)
    res = client.get(f"/api/v1/mikrotik/{rid}/timeline", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    assert d["nas"]["id"] == rid and d["entries"] == [] and d["count"] == 0


def test_timeline_returns_entry(app, client):
    rid = _seed(app, with_audit=True)
    d = client.get(f"/api/v1/mikrotik/{rid}/timeline", headers=AUTH).get_json()["data"]
    assert d["count"] >= 1
    e = d["entries"][0]
    for key in ("action", "actor", "headline_ar", "severity", "created_at"):
        assert key in e


def test_limit_param(app, client):
    rid = _seed(app, with_audit=True)
    res = client.get(f"/api/v1/mikrotik/{rid}/timeline?limit=5", headers=AUTH)
    assert res.status_code == 200
