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
    tmp = tempfile.mkdtemp(prefix="hr_payments_api_")
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


def _enable_settings(client, **overrides):
    payload = {
        "enabled": True,
        "provider": "manual_wallet",
        "wallet_number": "0599000000",
        "wallet_owner_name": "Hobe Wallet",
        "currency": "ILS",
        "confirmation_mode": "manual",
        "allow_cards": True,
        "allow_monthly_subscriptions": True,
        "allow_distributor_payments": True,
    }
    payload.update(overrides)
    return client.patch("/api/v1/payments/settings", json=payload, headers=_auth())


def test_get_and_patch_payment_settings(client):
    initial = client.get("/api/v1/payments/settings", headers=_auth())
    assert initial.status_code == 200
    assert initial.get_json()["data"]["settings"]["enabled"] is False

    updated = _enable_settings(client, min_amount=5, max_amount=100)
    assert updated.status_code == 200
    settings = updated.get_json()["data"]["settings"]
    assert settings["enabled"] is True
    assert settings["wallet_number"] == "0599000000"
    assert settings["min_amount"] == 5.0


def test_create_request_success(client):
    assert _enable_settings(client).status_code == 200
    response = client.post(
        "/api/v1/payments/requests",
        json={
            "payer_type": "subscriber",
            "payer_id": 7,
            "purpose": "card_purchase",
            "amount": 20,
        },
        headers=_auth(),
    )
    assert response.status_code == 201
    request = response.get_json()["data"]["request"]
    assert request["status"] == "pending"
    assert request["reference_code"].startswith("PAY-")
    assert request["receiver_wallet"] == "0599000000"


def test_create_request_fails_when_payments_disabled(client):
    response = client.post(
        "/api/v1/payments/requests",
        json={"purpose": "card_purchase", "amount": 20},
        headers=_auth(),
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "payments_disabled"
    assert response.get_json()["error"]["message"] == "تحصيل المدفوعات غير مفعل."
    assert "payment collection is disabled" not in response.get_json()["error"]["message"]


def test_create_request_fails_when_purpose_disabled(client):
    assert _enable_settings(client, allow_cards=False).status_code == 200
    response = client.post(
        "/api/v1/payments/requests",
        json={"purpose": "card_purchase", "amount": 20},
        headers=_auth(),
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "purpose_disabled"
    assert response.get_json()["error"]["message"] == "هذا النوع من الدفع غير مفعل."


@pytest.mark.parametrize(
    "payload",
    [
        {"purpose": "card_purchase", "amount": 0},
        {"purpose": "card_purchase", "amount": 20, "currency": "BTC"},
    ],
)
def test_create_request_rejects_invalid_amount_or_currency(client, payload):
    assert _enable_settings(client).status_code == 200
    response = client.post("/api/v1/payments/requests", json=payload, headers=_auth())
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "validation_error"
    assert response.get_json()["error"]["message"] in {
        "المبلغ غير صالح.",
        "المبلغ أقل من الحد الأدنى.",
        "العملة غير مسموحة.",
    }


def test_instructions_endpoint_exposes_safe_fields_only(client):
    assert _enable_settings(client).status_code == 200
    created = client.post(
        "/api/v1/payments/requests",
        json={"payer_type": "subscriber", "purpose": "card_purchase", "amount": 20},
        headers=_auth(),
    ).get_json()["data"]["request"]

    response = client.get(
        f"/api/v1/payments/requests/{created['id']}/instructions",
        headers=_auth(),
    )
    assert response.status_code == 200
    instructions = response.get_json()["data"]["instructions"]
    assert set(instructions) == {
        "amount",
        "currency",
        "receiver_wallet",
        "wallet_owner_name",
        "reference_code",
        "expires_at",
        "instructions",
        "status",
    }
    assert "created_by" not in instructions
    assert "Send the exact amount" not in instructions["instructions"]
    assert "أرسل المبلغ نفسه" in instructions["instructions"]


def test_request_detail_includes_proofs_and_service_apply_attempts(client):
    assert _enable_settings(client).status_code == 200
    created = client.post(
        "/api/v1/payments/requests",
        json={"payer_type": "subscriber", "purpose": "card_purchase", "amount": 20},
        headers=_auth(),
    ).get_json()["data"]["request"]

    proof = client.post(
        f"/api/v1/payments/requests/{created['id']}/proofs",
        json={
            "proof_type": "manual_reference",
            "reference_number": "JP-4455",
            "note": "تم التحويل من محفظة العميل",
        },
        headers=_auth(),
    )
    assert proof.status_code == 201

    approve = client.post(
        f"/api/v1/admin/payments/requests/{created['id']}/approve",
        json={"review_note": "تمت المطابقة يدويًا"},
        headers=_auth(),
    )
    assert approve.status_code == 200

    apply = client.post(
        f"/api/v1/admin/payments/requests/{created['id']}/apply-service",
        headers=_auth(),
    )
    assert apply.status_code == 200

    detail = client.get(
        f"/api/v1/payments/requests/{created['id']}",
        headers=_auth(),
    )
    assert detail.status_code == 200
    data = detail.get_json()["data"]
    assert data["request"]["id"] == created["id"]
    assert data["request"]["status"] == "paid"
    assert data["proofs"][0]["reference_number"] == "JP-4455"
    assert data["proofs"][0]["review_status"] == "approved"
    assert data["apply_attempts"][0]["payment_request_id"] == created["id"]
    assert data["apply_attempts"][0]["status"] == "applied"
    assert data["apply_attempts"][0]["result"]["mode"] == "record_only"


def test_request_list_filters(client):
    assert _enable_settings(client).status_code == 200
    client.post(
        "/api/v1/payments/requests",
        json={"payer_type": "subscriber", "purpose": "card_purchase", "amount": 20},
        headers=_auth(),
    )
    client.post(
        "/api/v1/payments/requests",
        json={"payer_type": "subscriber", "purpose": "monthly_subscription", "amount": 30},
        headers=_auth(),
    )

    response = client.get(
        "/api/v1/payments/requests?purpose=card_purchase&status=pending",
        headers=_auth(),
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["count"] == 1
    assert data["items"][0]["purpose"] == "card_purchase"
