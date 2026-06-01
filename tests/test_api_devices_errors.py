from __future__ import annotations

import os
import sys
import tempfile

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_devices_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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


def test_devices_error_messages_are_arabic(client):
    missing = client.get("/api/v1/devices/by-mac/AA:BB:CC:DD:EE:FF", headers=AUTH)
    assert missing.status_code == 404
    assert missing.get_json()["error"]["message"] == "لا توجد بصمة جهاز لهذا العنوان."

    empty = client.post("/api/v1/devices/ingest", data="", headers=AUTH)
    assert empty.status_code == 400
    assert empty.get_json()["error"]["message"] == "بيانات الأجهزة مطلوبة."

    invalid_json = client.post("/api/v1/devices/ingest", data="not-json", headers=AUTH)
    assert invalid_json.status_code == 400
    assert invalid_json.get_json()["error"]["message"] == "بيانات الطلب ليست JSON صالحًا."

    invalid_shape = client.post("/api/v1/devices/ingest", json={"leases": "bad"}, headers=AUTH)
    assert invalid_shape.status_code == 400
    assert invalid_shape.get_json()["error"]["message"] == "قائمة leases يجب أن تكون مصفوفة."
