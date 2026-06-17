"""feat/api-first-endpoints — MikroTik programming JSON.

يعكس صفحة /admin/radius/mt/<id>/program*: مخطّط النموذج + حالة الراوتر،
توليد الخطّة، التطبيق (بوّابات confirm/المخاطر/السلامة)، وإلغاء البرمجة.
المسارات الحيّة (تطبيق فعلي) تحتاج راوترًا حقيقيًّا؛ هنا نغطّي الحراسة
والتحقّق والبوّابات (بلا راوتر). شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mtprog_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    # NAS غير قابل للوصول (منفذ مغلق) → فحص الحالة يفشل سريعًا (offline).
    with created.app_context():
        from app.radius.core.types import NasDevice
        from app.radius.db.repos import nas_repo
        nas_repo.upsert_nas(NasDevice(
            id=None, tenant_id=1, name="MT-Prog", address="127.0.0.1",
            secret="s", vendor="mikrotik", nas_type="hotspot",
            api_user="admin", api_password="pw", api_port=1, enabled=True))
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def test_requires_auth(client):
    assert client.get("/api/v1/mikrotik/1/program").status_code == 401


def test_missing_nas_404(client):
    r = client.get("/api/v1/mikrotik/999/program", headers=AUTH)
    assert r.status_code == 404 and r.get_json()["error"]["code"] == "not_found"


def test_program_get_schema(client):
    r = client.get("/api/v1/mikrotik/1/program?kind=hotspot", headers=AUTH)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["kind"] == "hotspot"
    assert "interface" in d["form_fields"] and "cidr" in d["form_fields"]
    assert d["form_fields"]["dns_servers"] == "8.8.8.8,1.1.1.1"
    assert "router_state" in d  # فارغة لأن الراوتر غير متصل (best-effort)


def test_plan_invalid_spec_422(client):
    # واجهة فارغة/CIDR غير صالح → فشل التحقّق قبل أي تطبيق
    r = client.post("/api/v1/mikrotik/1/program/plan", headers=AUTH,
                    json={"kind": "hotspot", "interface": "", "cidr": "bad"})
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "validation_error"


def test_apply_invalid_spec_422(client):
    r = client.post("/api/v1/mikrotik/1/program/apply", headers=AUTH,
                    json={"kind": "hotspot", "interface": "", "cidr": "bad",
                          "confirm": True})
    assert r.status_code == 422


def test_unprogram_invalid_kind_422(client):
    r = client.post("/api/v1/mikrotik/1/program/unprogram", headers=AUTH,
                    json={"kind": "wireless", "confirm": True})
    assert r.status_code == 422


def test_unprogram_requires_confirm(client):
    r = client.post("/api/v1/mikrotik/1/program/unprogram", headers=AUTH,
                    json={"kind": "hotspot"})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "confirm_required"
