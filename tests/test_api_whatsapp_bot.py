"""feat/api-first-parity — WhatsApp auto-reply bot config/rules JSON (group 6).

يعكس صفحة /admin/radius/communications/bot عبر comms_bot نفسه: قراءة إعدادات
البوت + القواعد، وحفظها (full/partial). شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_wabot_api_")
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
    assert client.get("/api/v1/whatsapp/bot").status_code == 401


def test_get_defaults(client):
    res = client.get("/api/v1/whatsapp/bot", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    cfg = res.get_json()["data"]["config"]
    # أول تشغيل: defaults جاهزة (قواعد افتراضية + معطّل)
    assert cfg["enabled"] is False
    assert isinstance(cfg["commands"], list) and len(cfg["commands"]) >= 1
    assert "webhook_url" in res.get_json()["data"]


def test_save_full_config_and_rules(client):
    res = client.put("/api/v1/whatsapp/bot", headers=AUTH, json={
        "enabled": True,
        "greeting": "أهلاً",
        "fallback": "لم أفهم",
        "commands": [
            {"keyword": "رصيد", "reply_template": "رصيدك {balance}", "enabled": True},
            {"keyword": "", "reply_template": "", "enabled": True},  # تُتجاهل
        ],
    })
    assert res.status_code == 200, res.get_json()
    cfg = res.get_json()["data"]["config"]
    assert cfg["enabled"] is True and cfg["greeting"] == "أهلاً"
    # القاعدة الفارغة حُذفت؛ القاعدة الحقيقية بقيت
    kws = [c["keyword"] for c in cfg["commands"]]
    assert "رصيد" in kws and "" not in kws
    assert cfg["active_commands_count"] == len([c for c in cfg["commands"] if c["enabled"] and c["keyword"]])
    # الثبات عبر GET
    again = client.get("/api/v1/whatsapp/bot", headers=AUTH).get_json()["data"]["config"]
    assert again["enabled"] is True and "رصيد" in [c["keyword"] for c in again["commands"]]


def test_partial_patch_preserves_absent(client):
    client.put("/api/v1/whatsapp/bot", headers=AUTH, json={
        "enabled": True, "greeting": "G",
        "commands": [{"keyword": "k", "reply_template": "r", "enabled": True}]})
    # تعطيل فقط دون إرسال commands → القواعد تبقى
    res = client.patch("/api/v1/whatsapp/bot", headers=AUTH, json={"enabled": False})
    cfg = res.get_json()["data"]["config"]
    assert cfg["enabled"] is False
    assert "k" in [c["keyword"] for c in cfg["commands"]]
