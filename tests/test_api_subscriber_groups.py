"""feat/api-first-parity — v1 subscriber-groups JSON API (group 1).

يتحقّق أن نقاط /api/v1/subscriber-groups تعكس صفحة الويب
(routes/subscriber_groups.py) عبر طبقة الخدمة نفسها: قائمة/إنشاء/جلب/تعديل/
حذف + إجرائي المجموعة (فصل المتصلين، استعادة الكوتة اليومية). شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sg_api_")
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


def _create(client, name="VIP", **extra):
    return client.post("/api/v1/subscriber-groups", headers=AUTH,
                       json={"name": name, **extra})


def test_requires_auth(client):
    assert client.get("/api/v1/subscriber-groups").status_code == 401


def test_list_empty(client):
    res = client.get("/api/v1/subscriber-groups", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["ok"] is True
    assert body["data"]["items"] == [] and body["data"]["count"] == 0


def test_create_then_list_and_get(client):
    res = _create(client, name="VIP", description="kbar")
    assert res.status_code == 201, res.get_json()
    gid = res.get_json()["data"]["group"]["id"]
    # القائمة تعكسه
    lst = client.get("/api/v1/subscriber-groups", headers=AUTH).get_json()
    assert lst["data"]["count"] == 1
    assert lst["data"]["items"][0]["name"] == "VIP"
    # الجلب الفردي يرجع المجموعة + الأعضاء (فارغة)
    one = client.get(f"/api/v1/subscriber-groups/{gid}", headers=AUTH).get_json()
    assert one["data"]["group"]["name"] == "VIP"
    assert one["data"]["members"] == []


def test_create_requires_name(client):
    res = client.post("/api/v1/subscriber-groups", headers=AUTH, json={"name": "  "})
    assert res.status_code == 422


def test_create_duplicate_name_rejected(client):
    assert _create(client, name="Dup").status_code == 201
    dup = _create(client, name="Dup")
    assert dup.status_code == 422  # الخدمة ترفض الاسم المكرّر


def test_patch_updates(client):
    gid = _create(client, name="Edit").get_json()["data"]["group"]["id"]
    res = client.patch(f"/api/v1/subscriber-groups/{gid}", headers=AUTH,
                       json={"description": "updated"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["group"]["description"] == "updated"


def test_patch_missing_404(client):
    assert client.patch("/api/v1/subscriber-groups/9999", headers=AUTH,
                        json={"description": "x"}).status_code == 404


def test_get_missing_404(client):
    assert client.get("/api/v1/subscriber-groups/9999", headers=AUTH).status_code == 404


def test_delete(client):
    gid = _create(client, name="Gone").get_json()["data"]["group"]["id"]
    res = client.delete(f"/api/v1/subscriber-groups/{gid}", headers=AUTH)
    assert res.status_code == 200 and res.get_json()["data"]["deleted"] is True
    assert client.get(f"/api/v1/subscriber-groups/{gid}", headers=AUTH).status_code == 404


def test_disconnect_online_no_members(client):
    gid = _create(client, name="Empty1").get_json()["data"]["group"]["id"]
    res = client.post(f"/api/v1/subscriber-groups/{gid}/disconnect-online", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["disconnected"] == 0


def test_disconnect_online_missing_404(client):
    assert client.post("/api/v1/subscriber-groups/9999/disconnect-online",
                       headers=AUTH).status_code == 404


def test_quota_reset_daily_no_members(client):
    gid = _create(client, name="Empty2").get_json()["data"]["group"]["id"]
    res = client.post(f"/api/v1/subscriber-groups/{gid}/quota/reset-daily", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["reset"] == 0


def test_quota_reset_daily_missing_404(client):
    assert client.post("/api/v1/subscriber-groups/9999/quota/reset-daily",
                       headers=AUTH).status_code == 404
