"""feat/api-first-parity — events / risk / security / investigations (group 4).

يعكس صفحات /admin/radius/events* عبر EventsRiskCenterService نفسه:
قائمة الأحداث + الفلاتر، تفصيل الحدث، إشارات المخاطر + تشغيل القواعد، عرض
الأمان، وقائمة/فتح التحقيقات. شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_events_api_")
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


def _seed_event(app, *, category="subscriber", severity="info", event_key="sub.login",
                target_type="subscriber", target_id=5, message="hello"):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            cur = conn.execute(
                "INSERT INTO business_events(tenant_id, category, severity, actor_type, "
                "target_type, target_id, event_key, message, created_at) "
                "VALUES (1,?,?,'admin',?,?,?,?,?)",
                (category, severity, target_type, target_id, event_key, message, now))
            return cur.lastrowid


def test_requires_auth(client):
    assert client.get("/api/v1/events-center").status_code == 401


def test_events_list_and_filter(app, client):
    _seed_event(app, category="subscriber", event_key="sub.login")
    _seed_event(app, category="security", event_key="sec.alert", severity="warning")
    res = client.get("/api/v1/events-center", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["count"] == 2
    assert "summary" in data
    # فلتر category
    sec = client.get("/api/v1/events-center?category=security", headers=AUTH).get_json()["data"]
    assert sec["count"] == 1
    assert sec["events"][0]["event_key"] == "sec.alert"


def test_event_detail_and_timeline(app, client):
    eid = _seed_event(app, target_type="subscriber", target_id=42)
    res = client.get(f"/api/v1/events-center/{eid}", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["event"]["id"] == eid
    assert isinstance(data["timeline"], list)


def test_event_detail_404(client):
    assert client.get("/api/v1/events-center/99999", headers=AUTH).status_code == 404


def test_risk_list_and_run(app, client):
    res = client.get("/api/v1/events-center/risk", headers=AUTH)
    assert res.status_code == 200
    assert "flags" in res.get_json()["data"] and "summary" in res.get_json()["data"]
    run = client.post("/api/v1/events-center/risk/run", headers=AUTH)
    assert run.status_code == 200, run.get_json()
    assert "result" in run.get_json()["data"]


def test_security_view(app, client):
    _seed_event(app, category="security", event_key="sec.x", severity="error")
    res = client.get("/api/v1/events-center/security", headers=AUTH)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert any(e["event_key"] == "sec.x" for e in data["events"])
    assert "flags" in data


def test_investigations_create_and_list(app, client):
    create = client.post("/api/v1/events-center/investigations", headers=AUTH,
                         json={"title": "Suspicious card reuse", "severity": "error",
                               "entity_type": "card", "entity_id": 7, "summary": "x"})
    assert create.status_code == 201, create.get_json()
    lst = client.get("/api/v1/events-center/investigations", headers=AUTH).get_json()["data"]
    assert any(i["title"] == "Suspicious card reuse" for i in lst["investigations"])


def test_investigation_requires_title(client):
    assert client.post("/api/v1/events-center/investigations", headers=AUTH,
                       json={"title": " "}).status_code == 422
