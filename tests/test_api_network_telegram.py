"""feat/api-first-parity — network Telegram alerts JSON (group 5).

يعكس صفحة /admin/radius/network/telegram عبر tenant_telegram_settings_repo:
قراءة الإعدادات (التوكن مُقنّع)، حفظ PATCH-style، وإرسال اختبار. شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_tg_api_")
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


def test_requires_auth(client):
    assert client.get("/api/v1/network/telegram").status_code == 401


def test_get_default_empty(client):
    res = client.get("/api/v1/network/telegram", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    s = res.get_json()["data"]["settings"]
    assert s["has_bot_token"] is False and s["enabled"] is False and s["ready"] is False


def test_save_and_mask(client):
    res = client.patch("/api/v1/network/telegram", headers=AUTH,
                       json={"bot_token": "123456:ABCDEF-secret", "chat_id": "-100999",
                             "enabled": True, "thread_id": "7"})
    assert res.status_code == 200, res.get_json()
    s = res.get_json()["data"]["settings"]
    # التوكن لا يُعاد خامًا — فقط مقنّع + علم وجوده
    assert s["has_bot_token"] is True
    assert s["bot_token_masked"] == "…cret"
    assert "ABCDEF" not in str(res.get_json())
    assert s["chat_id"] == "-100999" and s["thread_id"] == "7"
    assert s["enabled"] is True and s["ready"] is True


def test_patch_preserves_absent_token(client):
    client.patch("/api/v1/network/telegram", headers=AUTH,
                 json={"bot_token": "tok-123456", "chat_id": "c1", "enabled": True})
    # تحديث chat_id فقط دون إرسال bot_token → يبقى التوكن
    res = client.patch("/api/v1/network/telegram", headers=AUTH, json={"chat_id": "c2"})
    s = res.get_json()["data"]["settings"]
    assert s["has_bot_token"] is True and s["chat_id"] == "c2"


def test_disable_keeps_config(client):
    client.patch("/api/v1/network/telegram", headers=AUTH,
                 json={"bot_token": "tok-9", "chat_id": "c", "enabled": True})
    res = client.patch("/api/v1/network/telegram", headers=AUTH, json={"enabled": False})
    s = res.get_json()["data"]["settings"]
    assert s["enabled"] is False and s["ready"] is False and s["has_bot_token"] is True


def test_test_send_without_config_fails(client):
    # لا توكن/تفعيل → الإرسال يفشل بـ502 (محاكاة مطابِقة لسلوك المُرسِل)
    res = client.post("/api/v1/network/telegram/test", headers=AUTH)
    assert res.status_code == 502
    assert res.get_json()["ok"] is False
