"""R1B Card Checker API tests."""
from __future__ import annotations

import secrets

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
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


def _unique_prefix() -> str:
    return "ck" + secrets.token_hex(4)


def _generate_card(client, auth_headers, **overrides):
    body = {
        "plan_id": 1,
        "count": 1,
        "username_prefix": _unique_prefix(),
    }
    body.update(overrides)
    res = client.post("/api/v1/cards/generate", json=body, headers=auth_headers)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    return data["batch"], data["cards"][0]


def _check(client, auth_headers, query: str):
    return client.get(
        "/api/v1/cards/check",
        query_string={"query": query},
        headers=auth_headers,
    )


def _has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def test_card_checker_requires_bearer_token(client):
    res = client.get("/api/v1/cards/check", query_string={"query": "anything"})
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_card_checker_existing_available_card(client, auth_headers):
    batch, card = _generate_card(client, auth_headers)
    res = _check(client, auth_headers, card["username"])
    assert res.status_code == 200, res.get_json()
    payload = res.get_json()["data"]["card"]
    assert payload["exists"] is True
    assert payload["status"] == "available"
    assert payload["username"] == card["username"]
    assert payload["has_password"] is True
    assert payload["batch"]["id"] == batch["id"]
    assert payload["profile"]["id"] == 1
    assert "cards" in payload["data_sources"]


def test_card_checker_not_found(client, auth_headers):
    res = _check(client, auth_headers, "missing-" + secrets.token_hex(8))
    assert res.status_code == 200
    payload = res.get_json()["data"]["card"]
    assert payload["exists"] is False
    assert payload["status"] == "not_found"


def test_card_checker_rejects_empty_query(client, auth_headers):
    res = _check(client, auth_headers, "   ")
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "validation_error"


def test_card_checker_rejects_long_query(client, auth_headers):
    res = _check(client, auth_headers, "x" * 129)
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "validation_error"


def test_card_checker_does_not_expose_password(client, auth_headers):
    _, card = _generate_card(client, auth_headers)
    res = _check(client, auth_headers, card["username"])
    assert res.status_code == 200, res.get_json()
    payload = res.get_json()["data"]["card"]
    assert payload["has_password"] is True
    assert _has_key(payload, "password") is False


def test_card_checker_includes_batch_and_profile_data(client, auth_headers):
    batch, card = _generate_card(client, auth_headers)
    res = _check(client, auth_headers, card["username"])
    payload = res.get_json()["data"]["card"]
    assert payload["batch"]["batch_code"] == batch["batch_code"]
    assert payload["batch"]["generated"] >= 1
    assert payload["profile"]["name"]
    assert "card_batches" in payload["data_sources"]
    assert "access_plans" in payload["data_sources"]


def test_card_checker_revoked_status(client, auth_headers):
    _, card = _generate_card(client, auth_headers)
    revoke = client.post(
        f"/api/v1/cards/{card['id']}/revoke",
        headers=auth_headers,
    )
    assert revoke.status_code == 200, revoke.get_json()
    res = _check(client, auth_headers, card["username"])
    assert res.status_code == 200
    assert res.get_json()["data"]["card"]["status"] == "revoked"


def test_card_checker_expired_status(client, auth_headers):
    _, card = _generate_card(
        client,
        auth_headers,
        time_value=-1,
        time_unit="days",
    )
    res = _check(client, auth_headers, card["username"])
    assert res.status_code == 200
    payload = res.get_json()["data"]["card"]
    assert payload["status"] == "expired"
    assert payload["expires_at"] is not None


def test_card_operations_disable_enable_and_do_not_expose_password(client, auth_headers):
    _, card = _generate_card(client, auth_headers)

    disabled = client.post(
        f"/api/v1/cards/{card['id']}/disable",
        json={"reason": "support hold"},
        headers=auth_headers,
    )
    assert disabled.status_code == 200, disabled.get_json()
    payload = disabled.get_json()["data"]["card"]
    assert payload["status"] == "revoked"
    assert payload["revoked"] is True
    assert payload["disabled_reason"] == "support hold"
    assert _has_key(payload, "password") is False

    enabled = client.post(
        f"/api/v1/cards/{card['id']}/enable",
        headers=auth_headers,
    )
    assert enabled.status_code == 200, enabled.get_json()
    payload = enabled.get_json()["data"]["card"]
    assert payload["revoked"] is False
    assert payload["status"] == "available"
    assert _has_key(payload, "password") is False


def test_card_operations_lock_unlock_mac(client, auth_headers):
    _, card = _generate_card(client, auth_headers)

    empty = client.post(
        f"/api/v1/cards/{card['id']}/lock-mac",
        json={"mac": " "},
        headers=auth_headers,
    )
    assert empty.status_code == 422

    locked = client.post(
        f"/api/v1/cards/{card['id']}/lock-mac",
        json={"mac": "AA:BB:CC:DD:EE:01"},
        headers=auth_headers,
    )
    assert locked.status_code == 200, locked.get_json()
    assert locked.get_json()["data"]["card"]["locked_mac"] == "AA:BB:CC:DD:EE:01"

    unlocked = client.post(
        f"/api/v1/cards/{card['id']}/unlock-mac",
        headers=auth_headers,
    )
    assert unlocked.status_code == 200, unlocked.get_json()
    assert unlocked.get_json()["data"]["card"]["locked_mac"] is None


def test_card_operations_reset_usage(client, auth_headers):
    from app.radius.db.repos import cards_repo
    from app.radius.db.connection import transaction

    _, card = _generate_card(client, auth_headers)
    # Seed a used state directly; reset endpoint must clear operational usage.
    cards_repo.set_card_locked_mac(1, card["id"], "AA:BB:CC:DD:EE:02", actor="qa")
    with transaction() as conn:
        conn.execute(
            """
            UPDATE cards
            SET used = 1,
                first_used_at = '2026-01-01T00:00:00',
                used_by_mac = 'AA:BB:CC:DD:EE:02',
                expire_at = '2026-02-01T00:00:00'
            WHERE tenant_id = 1 AND id = ?
            """,
            (card["id"],),
        )

    reset = client.post(
        f"/api/v1/cards/{card['id']}/reset-usage",
        headers=auth_headers,
    )
    assert reset.status_code == 200, reset.get_json()
    payload = reset.get_json()["data"]["card"]
    assert payload["used"] is False
    assert payload["started_at"] is None
    assert payload["expires_at"] is None
    assert payload["mac_address"] is None


def test_card_disconnect_endpoint_calls_service(client, auth_headers, monkeypatch):
    from app.radius.services.cards import CardsService

    _, card = _generate_card(client, auth_headers)
    calls = []

    def fake_disconnect(self, *, actor, username, session_id=""):
        calls.append({"actor": actor, "username": username, "session_id": session_id})

    monkeypatch.setattr(CardsService, "disconnect_card", fake_disconnect)
    res = client.post(
        f"/api/v1/cards/{card['id']}/disconnect",
        json={"session_id": "sess-123"},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.get_json()
    assert calls
    assert calls[0]["username"] == card["username"]
    assert calls[0]["session_id"] == "sess-123"


def test_card_permanent_delete_requires_strong_confirmation(client, auth_headers):
    _, card = _generate_card(client, auth_headers)

    rejected = client.post(
        f"/api/v1/cards/{card['id']}/delete-permanent",
        json={"confirm": "DELETE"},
        headers=auth_headers,
    )
    assert rejected.status_code == 422

    deleted = client.post(
        f"/api/v1/cards/{card['id']}/delete-permanent",
        json={"confirm": f"DELETE:{card['username']}"},
        headers=auth_headers,
    )
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["data"]["card"]["exists"] is False


def test_card_operations_missing_card_returns_404(client, auth_headers):
    res = client.post("/api/v1/cards/999999999/enable", headers=auth_headers)
    assert res.status_code == 404
