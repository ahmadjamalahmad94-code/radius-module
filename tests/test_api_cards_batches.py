"""
Slice C regression tests — cards batches list + drill-down.

Covers:
  - GET /api/v1/cards/batches
  - GET /api/v1/cards/batches/<id>
  - GET /api/v1/cards/batches/<id>/cards
  - GET /api/v1/cards/batches/<id>/cards?used=...&revoked=...
  - GET /api/v1/cards/batches/999999/cards → 404
"""
from __future__ import annotations

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
def fresh_batch_id(client, auth_headers):
    """Generate a 3-card batch for drill-down assertions."""
    res = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 3, "username_prefix": "qa"},
        headers=auth_headers,
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["batch"]["id"]


def test_batches_list_returns_items(client, auth_headers):
    res = client.get("/api/v1/cards/batches", headers=auth_headers)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert "items" in data
    assert isinstance(data["items"], list)
    # Schema check on the first item if any
    if data["items"]:
        b = data["items"][0]
        assert "id" in b
        assert "batch_code" in b
        assert "plan_id" in b
        assert "count" in b


def test_batches_list_pagination(client, auth_headers):
    res = client.get("/api/v1/cards/batches?limit=1&offset=0", headers=auth_headers)
    assert res.status_code == 200
    assert len(res.get_json()["data"]["items"]) <= 1


def test_batch_get_round_trips(client, auth_headers, fresh_batch_id):
    res = client.get(
        f"/api/v1/cards/batches/{fresh_batch_id}", headers=auth_headers
    )
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["id"] == fresh_batch_id
    assert data["count"] == 3
    assert data["generated"] == 3
    assert data["batch_code"].startswith("B-")


def test_batch_get_404_on_missing(client, auth_headers):
    res = client.get("/api/v1/cards/batches/999999", headers=auth_headers)
    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == "not_found"


def test_cards_of_batch_returns_cards(client, auth_headers, fresh_batch_id):
    res = client.get(
        f"/api/v1/cards/batches/{fresh_batch_id}/cards",
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["batch_id"] == fresh_batch_id
    assert data["count"] == 3
    assert len(data["items"]) == 3
    for c in data["items"]:
        assert c["batch_id"] == fresh_batch_id
        assert c["username"]
        assert c["password"]


def test_cards_of_batch_filters_used(client, auth_headers, fresh_batch_id):
    """Freshly-generated cards are all unused, so used=false returns all 3
    and used=true returns 0."""
    unused = client.get(
        f"/api/v1/cards/batches/{fresh_batch_id}/cards?used=false",
        headers=auth_headers,
    )
    assert unused.status_code == 200
    assert unused.get_json()["data"]["count"] == 3

    used = client.get(
        f"/api/v1/cards/batches/{fresh_batch_id}/cards?used=true",
        headers=auth_headers,
    )
    assert used.status_code == 200
    assert used.get_json()["data"]["count"] == 0


def test_cards_of_batch_404_on_missing(client, auth_headers):
    res = client.get(
        "/api/v1/cards/batches/999999/cards", headers=auth_headers
    )
    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == "not_found"
