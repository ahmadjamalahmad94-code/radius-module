from __future__ import annotations

import os
import sys
import tempfile

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_customer_portals_api_")
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


def test_customer_portals_api_requires_auth(client):
    res = client.get("/api/v1/customer-portals")
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_customer_portals_route_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/customer-portals" in routes


def test_customer_portals_api_returns_navigation_only_contract(client):
    res = client.get("/api/v1/customer-portals", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    items = {item["key"]: item for item in data["items"]}

    assert items["subscriber_portal"]["label"] == "بوابة المشترك"
    assert items["subscriber_portal"]["public_path"] == "/portal/subscriber/login"
    assert items["card_user_portal"]["label"] == "بوابة مستخدم البطاقة"
    assert items["card_user_portal"]["public_path"] == "/portal/card/login"
    assert data["security"]["admin_navigation_only"] is True

    forbidden_paths = {
        "/portal/card/purchase",
        "/portal/card/redeem",
        "/portal/subscriber/loan-request",
        "/portal/subscriber/renewal-request",
    }
    returned_paths = {
        value
        for item in data["items"]
        for value in item.values()
        if isinstance(value, str) and value.startswith("/")
    }
    assert forbidden_paths.isdisjoint(returned_paths)
