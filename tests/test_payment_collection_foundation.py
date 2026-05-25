from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_payments_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    yield create_app()

    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def test_payment_tables_exist(app):
    with app.app_context():
        from app.radius.db.connection import db

        rows = db().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {row["name"] for row in rows}

    for table in (
        "tenant_payment_settings",
        "payment_requests",
        "payment_proofs",
        "payment_collection_transactions",
        "payment_webhook_events",
    ):
        assert table in names


def test_create_and_update_settings(app):
    with app.app_context():
        from app.radius.db.repos.payments_repo import PaymentSettingsRepository

        repo = PaymentSettingsRepository()
        created = repo.upsert(
            tenant_id=1,
            enabled=True,
            provider="manual_wallet",
            wallet_number="0599000000",
            wallet_owner_name="Hobe",
            currency="ILS",
            confirmation_mode="manual",
            allow_cards=True,
            allow_monthly_subscriptions=False,
        )
        updated = repo.upsert(
            tenant_id=1,
            enabled=False,
            provider="manual_wallet",
            wallet_number="0599111111",
            wallet_owner_name="Hobe Updated",
            currency="USD",
            confirmation_mode="manual",
        )

    assert created.id == updated.id
    assert updated.enabled is False
    assert updated.wallet_number == "0599111111"
    assert updated.currency == "USD"


def test_create_payment_request_and_unique_reference(app):
    with app.app_context():
        from app.radius.db.repos.payments_repo import PaymentRequestRepository

        repo = PaymentRequestRepository()
        first = repo.create(
            tenant_id=1,
            payer_type="subscriber",
            payer_id=10,
            purpose="monthly_subscription",
            amount=25,
            currency="ILS",
            provider="manual_wallet",
            receiver_wallet="0599000000",
        )
        second = repo.create(
            tenant_id=1,
            payer_type="subscriber",
            payer_id=11,
            purpose="monthly_subscription",
            amount=30,
            currency="ILS",
            provider="manual_wallet",
            receiver_wallet="0599000000",
        )

    assert first["status"] == "pending"
    assert first["reference_code"].startswith("PAY-")
    assert first["reference_code"] != second["reference_code"]
    assert first["receiver_wallet"] == "0599000000"


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("amount", {"amount": 0}),
        ("provider", {"provider": "fake_gateway"}),
        ("purpose", {"purpose": "license_renewal"}),
        ("currency", {"currency": "BTC"}),
    ],
)
def test_request_rejects_invalid_values(app, field, kwargs):
    base = {
        "tenant_id": 1,
        "payer_type": "subscriber",
        "purpose": "card_purchase",
        "amount": 10,
        "currency": "ILS",
        "provider": "manual_wallet",
        "receiver_wallet": "0599000000",
    }
    base.update(kwargs)
    with app.app_context():
        from app.radius.db.repos.payments_repo import PaymentRequestRepository

        with pytest.raises(ValueError, match=field):
            PaymentRequestRepository().create(**base)


def test_create_proof_linked_to_request(app):
    with app.app_context():
        from app.radius.db.repos.payments_repo import (
            PaymentProofRepository,
            PaymentRequestRepository,
        )

        request = PaymentRequestRepository().create(
            tenant_id=1,
            payer_type="subscriber",
            purpose="card_purchase",
            amount=15,
            currency="ILS",
            provider="manual_wallet",
            receiver_wallet="0599000000",
        )
        proof = PaymentProofRepository().create(
            payment_request_id=request["id"],
            reference_number="ABC123",
            note="manual transfer",
        )

    assert proof["payment_request_id"] == request["id"]
    assert proof["reference_number"] == "ABC123"
    assert proof["review_status"] is None


def test_duplicate_webhook_event_is_idempotent(app):
    with app.app_context():
        from app.radius.db.repos.payments_repo import PaymentWebhookEventRepository

        repo = PaymentWebhookEventRepository()
        first = repo.create(
            provider="jawwal_pay",
            event_id="evt-1",
            payload={"status": "ignored_unsigned"},
            signature_valid=False,
        )
        second = repo.create(
            provider="jawwal_pay",
            event_id="evt-1",
            payload={"status": "different_duplicate"},
            signature_valid=False,
        )

    assert first["id"] == second["id"]
    assert repo.payload(second)["status"] == "ignored_unsigned"
