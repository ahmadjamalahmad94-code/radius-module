"""
Slice C regression tests — NAS CUD + test endpoint.

Covers:
  - GET /api/v1/nas (list)
  - POST /api/v1/nas (create, validation, secret never leaks)
  - GET /api/v1/nas/<id>
  - PATCH /api/v1/nas/<id> (partial update)
  - DELETE /api/v1/nas/<id> (404 on missing)
  - POST /api/v1/nas/<id>/test (TCP reachability)
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


@pytest.fixture
def cleanup_nas(client, auth_headers):
    """Tracks created NAS ids and best-effort deletes them after each test."""
    ids: list[int] = []
    yield ids
    for nid in ids:
        try:
            client.delete(f"/api/v1/nas/{nid}", headers=auth_headers)
        except Exception:  # noqa: BLE001
            pass


def _make_nas_body(**overrides) -> dict:
    body = {
        "name": f"qa-nas-{int(time.time() * 1000)}",
        "address": "192.0.2.1",  # TEST-NET-1, won't be reachable
        "secret": "do-not-leak-this",
        "vendor": "mikrotik",
        "nas_type": "hotspot",
        "auth_port": 1812,
        "acct_port": 1813,
        "api_port": 8728,
        "enabled": True,
    }
    body.update(overrides)
    return body


def test_create_nas_returns_201_and_strips_secret(client, auth_headers, cleanup_nas):
    res = client.post("/api/v1/nas", json=_make_nas_body(), headers=auth_headers)
    assert res.status_code == 201
    data = res.get_json()["data"]
    cleanup_nas.append(data["id"])
    assert data["name"].startswith("qa-nas-")
    assert data["vendor"] == "mikrotik"
    assert "secret" not in data, "secret must never appear in response"
    assert "api_password" not in data, "api_password must never appear in response"


def test_create_nas_missing_name_returns_422(client, auth_headers):
    res = client.post(
        "/api/v1/nas",
        json={"address": "192.0.2.2"},
        headers=auth_headers,
    )
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "validation_error"


def test_create_nas_missing_address_returns_422(client, auth_headers):
    res = client.post(
        "/api/v1/nas",
        json={"name": "x"},
        headers=auth_headers,
    )
    assert res.status_code == 422


def test_create_nas_unknown_vendor_returns_422(client, auth_headers):
    res = client.post(
        "/api/v1/nas",
        json=_make_nas_body(vendor="bogus"),
        headers=auth_headers,
    )
    assert res.status_code == 422
    assert "vendor" in res.get_json()["error"]["message"].lower()


def test_get_nas_round_trips(client, auth_headers, cleanup_nas):
    create = client.post(
        "/api/v1/nas",
        json=_make_nas_body(location="lab-rack-01"),
        headers=auth_headers,
    )
    nid = create.get_json()["data"]["id"]
    cleanup_nas.append(nid)
    res = client.get(f"/api/v1/nas/{nid}", headers=auth_headers)
    assert res.status_code == 200
    assert res.get_json()["data"]["location"] == "lab-rack-01"


def test_patch_nas_partial_update(client, auth_headers, cleanup_nas):
    nid = client.post(
        "/api/v1/nas",
        json=_make_nas_body(),
        headers=auth_headers,
    ).get_json()["data"]["id"]
    cleanup_nas.append(nid)
    res = client.patch(
        f"/api/v1/nas/{nid}",
        json={"location": "after-patch", "enabled": False},
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["location"] == "after-patch"
    assert data["enabled"] is False
    # Name should be unchanged (partial update)
    assert data["name"].startswith("qa-nas-")


def test_patch_nas_secret_does_not_leak_back(client, auth_headers, cleanup_nas):
    nid = client.post(
        "/api/v1/nas",
        json=_make_nas_body(),
        headers=auth_headers,
    ).get_json()["data"]["id"]
    cleanup_nas.append(nid)
    res = client.patch(
        f"/api/v1/nas/{nid}",
        json={"secret": "new-rotated-secret"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert "secret" not in res.get_json()["data"]


def test_delete_nas_then_404(client, auth_headers):
    nid = client.post(
        "/api/v1/nas",
        json=_make_nas_body(),
        headers=auth_headers,
    ).get_json()["data"]["id"]
    res = client.delete(f"/api/v1/nas/{nid}", headers=auth_headers)
    assert res.status_code == 200
    res = client.get(f"/api/v1/nas/{nid}", headers=auth_headers)
    assert res.status_code == 404


def test_delete_non_existent_returns_404(client, auth_headers):
    res = client.delete("/api/v1/nas/999999", headers=auth_headers)
    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == "not_found"


def test_test_endpoint_against_unreachable_target(client, auth_headers, cleanup_nas):
    """An unreachable IP (TEST-NET-1) should produce a clean timeout response,
    not a 500."""
    nid = client.post(
        "/api/v1/nas",
        json=_make_nas_body(address="192.0.2.123", api_port=8728),
        headers=auth_headers,
    ).get_json()["data"]["id"]
    cleanup_nas.append(nid)
    res = client.post(f"/api/v1/nas/{nid}/test", headers=auth_headers)
    assert res.status_code == 200
    data = res.get_json()["data"]
    # Either timeout or unreachable; both are acceptable end states.
    assert data["status"] in {"timeout", "unreachable"}
    assert data["ok"] is False
    assert data["ip"] == "192.0.2.123"
    assert data["port"] == 8728
    assert isinstance(data["ms"], int)


def test_test_endpoint_404_on_missing_nas(client, auth_headers):
    res = client.post("/api/v1/nas/999999/test", headers=auth_headers)
    assert res.status_code == 404
