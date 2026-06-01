from __future__ import annotations

import os
import secrets
import sys
import tempfile

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_admin_control_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
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


def test_admin_control_routes_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert {
        "/api/v1/settings",
        "/api/v1/tokens",
        "/api/v1/tokens/<int:token_id>/revoke",
        "/api/v1/tenants",
        "/api/v1/tenants/<int:tenant_id>",
        "/api/v1/webhooks/deliveries",
    }.issubset(routes)


def test_settings_api_lists_updates_and_rejects_unknown_keys(client):
    listed = client.get("/api/v1/settings", headers=AUTH)
    assert listed.status_code == 200, listed.get_json()
    keys = {item["key"] for item in listed.get_json()["data"]["items"]}
    assert "billing.currency" in keys

    patched = client.patch(
        "/api/v1/settings",
        json={"settings": {"billing.currency": "USD"}},
        headers=AUTH,
    )
    assert patched.status_code == 200, patched.get_json()
    assert patched.get_json()["data"]["updated"]["billing.currency"] == "USD"

    rejected = client.patch(
        "/api/v1/settings",
        json={"settings": {"unknown.key": "x"}},
        headers=AUTH,
    )
    assert rejected.status_code == 422
    assert rejected.get_json()["error"]["code"] == "validation_error"
    assert rejected.get_json()["error"]["message"] == "مفتاح إعداد غير معروف."
    assert "unknown setting key" not in rejected.get_json()["error"]["message"]

    invalid_shape = client.patch(
        "/api/v1/settings",
        json={"settings": ["billing.currency"]},
        headers=AUTH,
    )
    assert invalid_shape.status_code == 422
    assert invalid_shape.get_json()["error"]["message"] == "الإعدادات يجب أن تكون كائنًا."
    assert "settings must be an object" not in invalid_shape.get_json()["error"]["message"]


def test_token_api_shows_plaintext_once_and_never_lists_hash(client):
    created = client.post(
        "/api/v1/tokens",
        json={"name": "mobile", "scopes": ["admin:full"]},
        headers=AUTH,
    )
    assert created.status_code == 201, created.get_json()
    data = created.get_json()["data"]
    assert data["token"].startswith("hr_")
    assert data["token_shown_once"] is True
    assert "token_hash" not in data

    listed = client.get("/api/v1/tokens", headers=AUTH)
    assert listed.status_code == 200, listed.get_json()
    item = next(t for t in listed.get_json()["data"]["items"] if t["id"] == data["id"])
    assert "token" not in item
    assert "token_hash" not in item
    assert item["revoked"] is False

    revoked = client.post(f"/api/v1/tokens/{data['id']}/revoke", headers=AUTH)
    assert revoked.status_code == 200, revoked.get_json()
    assert revoked.get_json()["data"]["revoked"] is True

    missing_name = client.post("/api/v1/tokens", json={"name": ""}, headers=AUTH)
    assert missing_name.status_code == 422
    assert missing_name.get_json()["error"]["message"] == "اسم التوكن مطلوب."

    bad_scopes = client.post("/api/v1/tokens", json={"name": "bad", "scopes": "admin:full"}, headers=AUTH)
    assert bad_scopes.status_code == 422
    assert bad_scopes.get_json()["error"]["message"] == "صلاحيات التوكن يجب أن تكون قائمة نصوص."

    bad_date = client.post(
        "/api/v1/tokens",
        json={"name": "bad-date", "expires_at": "not-a-date"},
        headers=AUTH,
    )
    assert bad_date.status_code == 422
    assert bad_date.get_json()["error"]["message"] == "تاريخ انتهاء التوكن غير صالح. استخدم صيغة ISO."

    missing = client.post("/api/v1/tokens/999999999/revoke", headers=AUTH)
    assert missing.status_code == 404
    assert missing.get_json()["error"]["message"] == "التوكن غير موجود."


def test_tenants_api_manage_existing_backend_model(client):
    slug = "tenant" + secrets.token_hex(4)
    created = client.post(
        "/api/v1/tenants",
        json={
            "slug": slug,
            "name": "Tenant " + slug,
            "plan_tier": "starter",
            "status": "active",
        },
        headers=AUTH,
    )
    assert created.status_code == 201, created.get_json()
    tenant = created.get_json()["data"]
    assert tenant["slug"] == slug

    patched = client.patch(
        f"/api/v1/tenants/{tenant['id']}",
        json={"display_name": "Updated", "api_rpm": 0},
        headers=AUTH,
    )
    assert patched.status_code == 200, patched.get_json()
    assert patched.get_json()["data"]["display_name"] == "Updated"
    assert patched.get_json()["data"]["api_rpm"] == 0

    invalid_status = client.patch(
        f"/api/v1/tenants/{tenant['id']}",
        json={"status": "bad"},
        headers=AUTH,
    )
    assert invalid_status.status_code == 422
    assert invalid_status.get_json()["error"]["message"] == "حالة المستأجر غير معروفة."
    assert "unknown status" not in invalid_status.get_json()["error"]["message"]

    listed = client.get("/api/v1/tenants", headers=AUTH)
    assert listed.status_code == 200, listed.get_json()
    assert any(item["slug"] == slug for item in listed.get_json()["data"]["items"])


def test_webhook_deliveries_api_does_not_return_payload_or_secret(client):
    from app.radius.core.types_saas import WebhookSubscription
    from app.radius.db.repos import webhooks_repo

    sub = webhooks_repo.upsert_sub(
        WebhookSubscription(
            id=None,
            tenant_id=1,
            target_url="https://example.test/hook",
            secret="super-secret",
            enabled_events=("webhook.test",),
        )
    )
    delivery_id = webhooks_repo.enqueue(
        1,
        sub.id,
        event="webhook.test",
        event_id="evt-1",
        payload={"password": "must-not-return"},
    )
    res = client.get("/api/v1/webhooks/deliveries", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    item = next(i for i in res.get_json()["data"]["items"] if i["id"] == delivery_id)
    assert item["event"] == "webhook.test"
    assert "payload" not in item
    assert "secret" not in item

    bad_limit = client.get("/api/v1/webhooks/deliveries?limit=abc", headers=AUTH)
    assert bad_limit.status_code == 422
    assert bad_limit.get_json()["error"]["message"] == "قيمة limit يجب أن تكون رقمًا صحيحًا."
    assert "limit must be integer" not in bad_limit.get_json()["error"]["message"]

    bad_status = client.get("/api/v1/webhooks/deliveries?status=bad", headers=AUTH)
    assert bad_status.status_code == 422
    assert bad_status.get_json()["error"]["message"] == "حالة التسليم غير معروفة."
    assert "unknown status" not in bad_status.get_json()["error"]["message"]
