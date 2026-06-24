"""اختبارات نقطة تسجيل رمز دفع الجهاز: POST/DELETE /api/v1/devices/push-token.

تُغطّي: التسجيل، الـupsert (نفس الرمز لا يُكرّر)، إلغاء التسجيل، التحقّق
من المُدخلات، والـidempotency. مصادقة عبر رمز التطوير (مطابق لبقية
اختبارات /api/v1).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_pushtok_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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


def test_register_upsert_and_unregister(app, client):
    # تسجيل أوّل.
    r = client.post("/api/v1/devices/push-token",
                    json={"token": "tok-abc", "platform": "android",
                          "app_version": "1.2.0"}, headers=AUTH)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()["data"]
    assert body["registered"] is True
    assert body["platform"] == "android"
    assert body["count"] == 1

    # إعادة إرسال نفس الرمز (منصّة مختلفة) ⇒ upsert لا تكرار.
    r = client.post("/api/v1/devices/push-token",
                    json={"token": "tok-abc", "platform": "ios"}, headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()["data"]["count"] == 1

    with app.app_context():
        from app.radius.db.repos import device_push_tokens_repo as repo
        assert repo.count_for_tenant(1) == 1
        assert repo.tokens_for_tenant(1) == ["tok-abc"]

    # إلغاء التسجيل (تسجيل خروج).
    r = client.delete("/api/v1/devices/push-token",
                      json={"token": "tok-abc"}, headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()["data"]["removed"] == 1

    with app.app_context():
        from app.radius.db.repos import device_push_tokens_repo as repo
        assert repo.count_for_tenant(1) == 0


def test_register_requires_token(client):
    r = client.post("/api/v1/devices/push-token", json={}, headers=AUTH)
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "missing_token"


def test_register_rejects_bad_platform(client):
    r = client.post("/api/v1/devices/push-token",
                    json={"token": "t", "platform": "blackberry"}, headers=AUTH)
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "invalid_platform"


def test_unregister_missing_token_is_idempotent(client):
    r = client.delete("/api/v1/devices/push-token",
                      json={"token": "never-seen"}, headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()["data"]["removed"] == 0


def test_push_token_requires_auth(client):
    # بلا ترويسة مصادقة ⇒ 401 (require_api_token يَفرض دائمًا).
    r = client.post("/api/v1/devices/push-token", json={"token": "t"})
    assert r.status_code == 401


def test_register_two_distinct_tokens_counts_both(app, client):
    client.post("/api/v1/devices/push-token",
                json={"token": "tok-1", "platform": "android"}, headers=AUTH)
    r = client.post("/api/v1/devices/push-token",
                    json={"token": "tok-2", "platform": "ios"}, headers=AUTH)
    assert r.get_json()["data"]["count"] == 2
    with app.app_context():
        from app.radius.db.repos import device_push_tokens_repo as repo
        assert set(repo.tokens_for_tenant(1)) == {"tok-1", "tok-2"}
