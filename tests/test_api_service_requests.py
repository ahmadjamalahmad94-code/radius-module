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
    tmp = tempfile.mkdtemp(prefix="hr_service_requests_")
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


def _create_subscriber(username: str = "customer-1") -> int:
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    subscriber = subscribers_repo.upsert_subscriber(Subscriber(
        id=None,
        tenant_id=1,
        username=username,
        password="secret",
        full_name="عميل تجريبي",
        mobile="0599000000",
    ))
    assert subscriber.id is not None
    return int(subscriber.id)


def _enable_payment_settings(client):
    return client.patch(
        "/api/v1/payments/settings",
        json={
            "enabled": True,
            "provider": "manual_wallet",
            "wallet_number": "0599000000",
            "wallet_owner_name": "Hobe Wallet",
            "currency": "ILS",
            "confirmation_mode": "manual",
            "allow_monthly_subscriptions": True,
        },
        headers=_auth(),
    )


def test_service_request_creates_support_ticket_without_payment(client):
    subscriber_id = _create_subscriber()

    response = client.post(
        "/api/v1/service-requests",
        json={
            "subscriber_id": subscriber_id,
            "service_key": "ip_change_vpn",
            "request_type": "activation",
            "notes": "يريد تفعيل الخدمة على الاشتراك الحالي",
        },
        headers=_auth(),
    )

    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["payment_request"] is None
    assert data["service_request"]["reference"].startswith("SR-")
    assert data["service_request"]["service_label"] == "خدمة تغيير IP / VPN"
    assert data["ticket"]["category"] == "service_request"
    assert "الخدمة المطلوبة: خدمة تغيير IP / VPN" in data["ticket"]["body"]
    assert "لا يوجد طلب دفع مرتبط" in data["ticket"]["body"]

    ticket = client.get(
        f"/api/v1/tickets/{data['service_request']['ticket_id']}",
        headers=_auth(),
    )
    assert ticket.status_code == 200
    assert ticket.get_json()["data"]["ticket"]["status"] == "open"


def test_service_request_can_open_manual_payment_request(client):
    subscriber_id = _create_subscriber("customer-2")
    assert _enable_payment_settings(client).status_code == 200

    response = client.post(
        "/api/v1/service-requests",
        json={
            "subscriber_id": subscriber_id,
            "service_key": "payment_collection",
            "request_type": "upgrade",
            "payment": {
                "amount": 35,
                "currency": "ILS",
                "purpose": "monthly_subscription",
            },
        },
        headers=_auth(),
    )

    assert response.status_code == 201
    data = response.get_json()["data"]
    payment_request = data["payment_request"]
    assert payment_request["status"] == "pending"
    assert payment_request["amount"] == 35.0
    assert payment_request["payer_type"] == "subscriber"
    assert payment_request["payer_id"] == subscriber_id
    assert data["service_request"]["payment_request_id"] == payment_request["id"]

    ticket = client.get(
        f"/api/v1/tickets/{data['service_request']['ticket_id']}",
        headers=_auth(),
    ).get_json()["data"]
    assert any(payment_request["reference_code"] in reply["body"] for reply in ticket["replies"])

    listed = client.get("/api/v1/payments/requests", headers=_auth())
    assert listed.status_code == 200
    assert listed.get_json()["data"]["items"][0]["id"] == payment_request["id"]


def test_service_request_with_payment_fails_when_collection_disabled(client):
    subscriber_id = _create_subscriber("customer-3")

    response = client.post(
        "/api/v1/service-requests",
        json={
            "subscriber_id": subscriber_id,
            "service_key": "cards",
            "payment": {"amount": 20},
        },
        headers=_auth(),
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "payments_disabled"

    from app.radius.db.connection import db

    count = db().execute("SELECT COUNT(*) AS c FROM tickets").fetchone()["c"]
    assert count == 0


def test_service_request_rejects_invalid_payload(client):
    subscriber_id = _create_subscriber("customer-4")

    invalid = client.post(
        "/api/v1/service-requests",
        json={"subscriber_id": subscriber_id, "service_key": "../bad"},
        headers=_auth(),
    )
    assert invalid.status_code == 422

    missing = client.post(
        "/api/v1/service-requests",
        json={"service_key": "cards"},
        headers=_auth(),
    )
    assert missing.status_code == 422
