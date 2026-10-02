"""FIX117 (API) — ثغرة «توليد مجّانيّ» من باب التطبيق.

قرار المالك (cardgen-role-split): المدير الفرعيّ يولّد من العروض المسعَّرة
فقط فتُخصم الجملة من محفظته. سُدّ النموذج الكامل في الويب (6ba3a872)، لكنّ
``POST /api/v1/cards/generate`` كان يقبل أيّ مديرٍ بمفتاح الدور «توليد
بطاقات» (دور «مشغّل» يحمله افتراضًا) فيولّد حزمةً **بلا أيّ خصم**.
والاستيراد ``/api/v1/cards/batches/import`` بلا صلاحية «استيراد الحِزم».

شغّلْ هذا الملفَّ وحدَه."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest

DEV = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_cardgen_guard_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.connection import db
        now = datetime.utcnow().isoformat() + "Z"
        db().execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t','T',?)", (now,))
        db().execute("INSERT INTO access_plans(id, tenant_id, name, code, speed_down_kbps, speed_up_kbps,"
                     " duration_minutes, price, created_at) VALUES (801,1,'باقة-اختبار-الحارس','guard_h1',2048,1024,60,1,?)", (now,))
        from app.radius.db.repos import admins_repo
        admins_repo.ensure_default_roles()
        role = admins_repo.get_role_by_name("operator")
        admins_repo.create_admin(username="op_mgr", password="op-pass-123", full_name="مشغّل",
                                 role_id=getattr(role, "id", None))
        db().commit()
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _mgr_headers(client):
    r = client.post("/api/admin/login", json={"username": "op_mgr", "password": "op-pass-123"})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": "Bearer " + r.get_json()["data"]["token"]}


def _batches(app):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute("SELECT COUNT(*) FROM card_batches").fetchone()[0]


def test_manager_role_key_alone_cannot_generate_free_batch_via_api(app):
    c = app.test_client()
    h = _mgr_headers(c)
    before = _batches(app)
    r = c.post("/api/v1/cards/generate", headers=h, json={"plan_id": 801, "count": 3})
    assert r.status_code == 403, r.get_json()
    assert "العروض" in r.get_json()["error"]["message"]
    assert _batches(app) == before, "حزمةٌ مجّانيّة أُنشئت من التطبيق"


def test_manager_with_explicit_owner_grant_may_generate(app, monkeypatch):
    from app.radius.services import manager_grants
    monkeypatch.setattr(manager_grants, "full_batch_form_granted", lambda *a, **k: True)
    c = app.test_client()
    r = c.post("/api/v1/cards/generate", headers=_mgr_headers(c), json={"plan_id": 801, "count": 2})
    assert r.status_code == 201, r.get_json()


def test_unrestricted_token_still_generates(app):
    c = app.test_client()
    r = c.post("/api/v1/cards/generate", headers=DEV, json={"plan_id": 801, "count": 2})
    assert r.status_code == 201, r.get_json()


def test_manager_without_import_grant_cannot_import_via_api(app):
    c = app.test_client()
    before = _batches(app)
    r = c.post("/api/v1/cards/batches/import", headers=_mgr_headers(c),
               json={"plan_id": 801, "cards": [{"username": "zz1", "password": "p"}]})
    assert r.status_code == 403, r.get_json()
    assert _batches(app) == before
