"""feat/api-first-parity — mikrotik login-designer JSON (group 7c).

يعكس بيانات/أفعال صفحة login-designer: الحالة (تصميم/معرض/مخطّط متغيّرات/
قوالب محفوظة)، الحفظ، وقوالب محفوظة (حفظ/تطبيق/حذف). شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mtld_api_")
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


def _seed_router(app, rid=810):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            conn.execute("INSERT INTO nas_devices(id, tenant_id, name, address, secret, vendor, enabled, created_at) "
                         "VALUES (?,1,'MT-LD','10.0.0.9','s','mikrotik',1,?)", (rid, now))
    return rid


def test_requires_auth(client):
    assert client.get("/api/v1/mikrotik/810/login-designer").status_code == 401


def test_state_shape(app, client):
    rid = _seed_router(app)
    res = client.get(f"/api/v1/mikrotik/{rid}/login-designer", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    assert d["nas"]["id"] == rid
    assert d["design"]["template_slug"]            # افتراضي classic
    assert isinstance(d["gallery"], list) and len(d["gallery"]) >= 1
    assert any("slug" in v and "label_ar" in v for v in d["variables"])
    assert d["presets"] == []


def test_state_unknown_router_404(client):
    assert client.get("/api/v1/mikrotik/9999/login-designer", headers=AUTH).status_code == 404


def test_save_design(app, client):
    rid = _seed_router(app)
    res = client.post(f"/api/v1/mikrotik/{rid}/login-designer/save", headers=AUTH,
                      json={"template_slug": "classic", "variables": {}})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["design"]["template_slug"] == "classic"
    # الحالة تعكس الحفظ
    st = client.get(f"/api/v1/mikrotik/{rid}/login-designer", headers=AUTH).get_json()["data"]
    assert st["design"]["template_slug"] == "classic"


def test_save_unknown_slug_422(app, client):
    rid = _seed_router(app)
    res = client.post(f"/api/v1/mikrotik/{rid}/login-designer/save", headers=AUTH,
                      json={"template_slug": "no-such-template"})
    assert res.status_code == 422


def test_preset_save_apply_delete(app, client):
    rid = _seed_router(app)
    # حفظ قالب
    sv = client.post(f"/api/v1/mikrotik/{rid}/login-designer/presets", headers=AUTH,
                     json={"name": "ليلي", "template_slug": "classic", "variables": {}})
    assert sv.status_code == 201, sv.get_json()
    presets = sv.get_json()["data"]["presets"]
    assert any(p.get("name") == "ليلي" for p in presets)
    pid = next(p["id"] for p in presets if p.get("name") == "ليلي")
    # تطبيق
    ap = client.post(f"/api/v1/mikrotik/{rid}/login-designer/presets/{pid}/apply", headers=AUTH)
    assert ap.status_code == 200 and ap.get_json()["data"]["design"]["template_slug"] == "classic"
    # حذف
    de = client.delete(f"/api/v1/mikrotik/{rid}/login-designer/presets/{pid}", headers=AUTH)
    assert de.status_code == 200 and de.get_json()["data"]["deleted"] is True
    # تطبيق محذوف → 404
    assert client.post(f"/api/v1/mikrotik/{rid}/login-designer/presets/{pid}/apply",
                       headers=AUTH).status_code == 404


def test_preset_name_required(app, client):
    rid = _seed_router(app)
    res = client.post(f"/api/v1/mikrotik/{rid}/login-designer/presets", headers=AUTH,
                      json={"name": "  ", "template_slug": "classic"})
    assert res.status_code == 422
