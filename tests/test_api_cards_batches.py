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


def test_batches_operations_list_supports_filters_and_totals(client, auth_headers):
    created = client.post(
        "/api/v1/cards/generate",
        json={
            "plan_id": 1,
            "count": 2,
            "username_prefix": "ops",
            "package_name": "API Ops Batch",
            "price_per_card": 2.5,
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.get_json()
    batch = created.get_json()["data"]["batch"]
    cards = created.get_json()["data"]["cards"]

    res = client.get(
        "/api/v1/cards/batches",
        query_string={"q": batch["batch_code"], "per_page": 10},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["total"] >= 1
    assert data["totals"]["batch_count"] >= 1
    assert data["items"][0]["batch_code"] == batch["batch_code"]
    assert data["items"][0]["available_count"] >= 2
    assert data["items"][0]["estimated_unit_price"] == 2.5
    assert "password" not in data["items"][0]
    for card in cards:
        assert card["password"] not in str(data)


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


def test_batches_export_csv_uses_current_filters_and_hides_passwords(
    client,
    auth_headers,
):
    created = client.post(
        "/api/v1/cards/generate",
        json={
            "plan_id": 1,
            "count": 1,
            "username_prefix": "csv",
            "package_name": "CSV API Batch",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.get_json()
    batch = created.get_json()["data"]["batch"]
    card = created.get_json()["data"]["cards"][0]

    res = client.get(
        "/api/v1/cards/batches/export.csv",
        query_string={"q": batch["batch_code"]},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("text/csv")
    csv_text = res.get_data(as_text=True)
    assert "batch_code" in csv_text
    assert batch["batch_code"] in csv_text
    assert card["password"] not in csv_text


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


def test_batches_bulk_archive_and_restore_are_soft(client, auth_headers):
    from app.radius.db.repos import cards_repo

    created = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 2, "username_prefix": "bulkapi"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.get_json()
    batch_id = created.get_json()["data"]["batch"]["id"]

    archived = client.post(
        "/api/v1/cards/batches/bulk",
        json={
            "action": "archive",
            "batch_ids": [batch_id],
            "reason": "api test archive",
        },
        headers=auth_headers,
    )
    assert archived.status_code == 200, archived.get_json()
    assert archived.get_json()["data"]["changed"] == 1
    batch = cards_repo.get_batch(1, batch_id)
    assert batch is not None
    assert batch.deleted_at is not None
    assert batch.delete_reason == "api test archive"
    assert len(cards_repo.list_cards(1, batch_id=batch_id)) == 2

    restored = client.post(
        "/api/v1/cards/batches/bulk",
        json={"action": "restore", "batch_ids": [batch_id]},
        headers=auth_headers,
    )
    assert restored.status_code == 200, restored.get_json()
    assert restored.get_json()["data"]["changed"] == 1
    batch = cards_repo.get_batch(1, batch_id)
    assert batch is not None
    assert batch.deleted_at is None


def test_batches_bulk_rejects_unknown_action(client, auth_headers, fresh_batch_id):
    res = client.post(
        "/api/v1/cards/batches/bulk",
        json={"action": "delete", "batch_ids": [fresh_batch_id]},
        headers=auth_headers,
    )
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "validation_error"
