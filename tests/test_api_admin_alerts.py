"""feat/api-first-endpoints — telegram admin-alerts catalogue JSON.

يعكس صفحة /admin/radius/alerts/telegram عبر خدمة admin_alerts +
tenant_telegram_settings_repo: الجرد (مفتاح/مجموعة/تفعيل/معاينة)، حفظ البوت
PATCH-style، التفعيل/التعطيل، واختبار الاتصال/التنبيه. شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_aa_api_")
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
    assert client.get("/api/v1/alerts/telegram").status_code == 401


def test_catalogue_shape(client):
    res = client.get("/api/v1/alerts/telegram", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    assert "bot" in d and "groups" in d and "catalogue" in d
    assert d["bot"]["has_token"] is False  # لا توكن خام
    keys = {a["key"] for a in d["catalogue"]}
    assert "subscriber_new" in keys and "store_chat_unanswered" in keys
    one = d["catalogue"][0]
    assert {"key", "group", "label", "enabled", "preview"} <= set(one)
    assert any(g["key"] == "store" for g in d["groups"])


def test_save_bot_masks_and_keeps(client):
    r = client.patch("/api/v1/alerts/telegram/bot", headers=AUTH,
                     json={"bot_token": "123456:SECRET-xy", "chat_id": "-100",
                           "enabled": True, "thread_id": "3"})
    assert r.status_code == 200, r.get_json()
    bot = r.get_json()["data"]["bot"]
    assert bot["has_token"] is True and "SECRET" not in str(bot)
    assert bot["chat_id"] == "-100" and bot["thread_id"] == "3"
    # حفظ ثانٍ بتوكن فارغ → يبقى التوكن، ويتغيّر chat_id
    r2 = client.patch("/api/v1/alerts/telegram/bot", headers=AUTH,
                      json={"bot_token": "", "chat_id": "-200"})
    bot2 = r2.get_json()["data"]["bot"]
    assert bot2["has_token"] is True and bot2["chat_id"] == "-200"


def test_toggle_persists(client):
    # quota_exhausted افتراضه OFF
    cat = client.get("/api/v1/alerts/telegram", headers=AUTH).get_json()["data"]["catalogue"]
    before = next(a for a in cat if a["key"] == "quota_exhausted")["enabled"]
    assert before is False
    r = client.post("/api/v1/alerts/telegram/alerts/quota_exhausted/toggle",
                    headers=AUTH, json={"enabled": True})
    assert r.status_code == 200 and r.get_json()["data"]["enabled"] is True
    cat2 = client.get("/api/v1/alerts/telegram", headers=AUTH).get_json()["data"]["catalogue"]
    after = next(a for a in cat2 if a["key"] == "quota_exhausted")["enabled"]
    assert after is True


def test_toggle_unknown_404(client):
    r = client.post("/api/v1/alerts/telegram/alerts/bogus_key/toggle",
                    headers=AUTH, json={"enabled": True})
    assert r.status_code == 404 and r.get_json()["error"]["code"] == "not_found"


def test_test_connection_without_bot_fails(client):
    r = client.post("/api/v1/alerts/telegram/test-connection", headers=AUTH)
    assert r.status_code == 502
    assert r.get_json()["error"]["code"] == "telegram_send_failed"


def test_alert_test_unknown_404(client):
    r = client.post("/api/v1/alerts/telegram/alerts/nope/test", headers=AUTH)
    assert r.status_code == 404
