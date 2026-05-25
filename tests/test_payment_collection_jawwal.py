from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOKEN = "dev-token-please-change"


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_payments_jawwal_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    app = create_app()
    app.testing = True
    yield app.test_client()

    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_jawwal_pay_provider_shell_cannot_create_payment_requests(client):
    patched = client.patch(
        "/api/v1/payments/settings",
        json={
            "enabled": True,
            "provider": "jawwal_pay",
            "wallet_number": "",
            "wallet_owner_name": "",
            "currency": "ILS",
            "confirmation_mode": "api",
        },
        headers=_auth(),
    )
    assert patched.status_code == 200

    created = client.post(
        "/api/v1/payments/requests",
        json={"payer_type": "subscriber", "purpose": "card_purchase", "amount": 20},
        headers=_auth(),
    )
    assert created.status_code == 422
    assert created.get_json()["error"]["code"] == "provider_disabled"


def test_jawwal_pay_webhook_shell_stores_unprocessed_event_and_marks_nothing_paid(client):
    client.patch(
        "/api/v1/payments/settings",
        json={
            "enabled": True,
            "provider": "manual_wallet",
            "wallet_number": "0599000000",
            "wallet_owner_name": "Hobe Wallet",
            "currency": "ILS",
            "confirmation_mode": "manual",
        },
        headers=_auth(),
    )
    request_row = client.post(
        "/api/v1/payments/requests",
        json={"payer_type": "subscriber", "purpose": "card_purchase", "amount": 20},
        headers=_auth(),
    ).get_json()["data"]["request"]

    webhook = client.post(
        "/api/v1/payments/webhooks/jawwal-pay",
        json={"payment_request_id": request_row["id"], "status": "paid"},
        headers=_auth(),
    )
    assert webhook.status_code == 202
    data = webhook.get_json()["data"]
    assert data["status"] == "stored_unprocessed"
    assert data["paid"] is False
    assert data["event"]["signature_valid"] is False
    assert data["event"]["processed"] is False

    fetched = client.get(f"/api/v1/payments/requests/{request_row['id']}", headers=_auth())
    assert fetched.get_json()["data"]["request"]["status"] == "pending"

    from app.radius.db.connection import db

    count = db().execute(
        "SELECT COUNT(*) AS c FROM payment_webhook_events WHERE provider='jawwal_pay'",
    ).fetchone()["c"]
    assert count == 1
