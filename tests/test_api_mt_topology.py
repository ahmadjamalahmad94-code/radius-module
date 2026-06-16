"""feat/api-first-parity — mikrotik topology JSON (group 7b).

يعكس صفحة /admin/radius/topology عبر نفس المجمّع: خادم + راوترات + روابط +
صحّة + فلاتر show/health/q. شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mttopo_api_")
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


def _seed(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            for rid, name, mode, addr in (
                (801, "MT-Core", "direct", "10.0.0.1"),
                (802, "AP-Edge", "vpn", "10.0.0.2"),
            ):
                conn.execute(
                    "INSERT INTO nas_devices(id, tenant_id, name, address, secret, vendor, "
                    "enabled, connection_mode, created_at) VALUES (?,1,?,?,'s','mikrotik',1,?,?)",
                    (rid, name, addr, mode, now))


def test_requires_auth(client):
    assert client.get("/api/v1/mikrotik/topology").status_code == 401


def test_topology_shape(app, client):
    _seed(app)
    res = client.get("/api/v1/mikrotik/topology", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    assert d["server"]["kind"] == "server"
    assert d["total_count"] == 2 and d["filtered_count"] == 2
    names = {r["label"] for r in d["routers"]}
    assert {"MT-Core", "AP-Edge"} <= names
    assert len(d["links"]) == 2
    # لا أسرار في عقدة الراوتر
    for r in d["routers"]:
        assert "secret" not in r and "api_password" not in r


def test_filter_vpn(app, client):
    _seed(app)
    d = client.get("/api/v1/mikrotik/topology?show=vpn", headers=AUTH).get_json()["data"]
    assert d["show"] == "vpn"
    assert [r["label"] for r in d["routers"]] == ["AP-Edge"]
    assert d["filtered_count"] == 1 and d["total_count"] == 2


def test_filter_query(app, client):
    _seed(app)
    d = client.get("/api/v1/mikrotik/topology?q=core", headers=AUTH).get_json()["data"]
    assert [r["label"] for r in d["routers"]] == ["MT-Core"]


def test_invalid_filters_default(app, client):
    _seed(app)
    d = client.get("/api/v1/mikrotik/topology?show=bogus&health=bogus", headers=AUTH).get_json()["data"]
    assert d["show"] == "all" and d["health"] == "all"
    assert "health_states" in d
