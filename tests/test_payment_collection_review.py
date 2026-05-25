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
    tmp = tempfile.mkdtemp(prefix="hr_payments_review_")
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


def _create_request(client) -> dict:
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
    return client.post(
        "/api/v1/payments/requests",
        json={"payer_type": "subscriber", "purpose": "card_purchase", "amount": 20},
        headers=_auth(),
    ).get_json()["data"]["request"]


def test_submit_proof_success(client):
    request = _create_request(client)
    response = client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "REF123", "note": "sent manually"},
        headers=_auth(),
    )
    assert response.status_code == 201
    proof = response.get_json()["data"]["proof"]
    assert proof["reference_number"] == "REF123"

    fetched = client.get(f"/api/v1/payments/requests/{request['id']}", headers=_auth())
    assert fetched.get_json()["data"]["request"]["status"] == "proof_submitted"


def test_submit_proof_fails_for_paid_request(client):
    request = _create_request(client)
    client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "REF123"},
        headers=_auth(),
    )
    approved = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/approve",
        json={"review_note": "ok"},
        headers=_auth(),
    )
    assert approved.status_code == 200

    second = client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "REF456"},
        headers=_auth(),
    )
    assert second.status_code == 422
    assert second.get_json()["error"]["code"] == "invalid_state"


def test_approve_creates_manual_transaction_and_cannot_approve_twice(client):
    request = _create_request(client)
    client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "REF123"},
        headers=_auth(),
    )
    approved = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/approve",
        json={"review_note": "matched wallet"},
        headers=_auth(),
    )
    assert approved.status_code == 200
    data = approved.get_json()["data"]
    assert data["request"]["status"] == "paid"
    assert data["transaction"]["status"] == "paid_manual"
    assert data["request"]["ledger_entry_id"]
    assert data["ledger_entry"]["entry_type"] == "payment"
    assert data["ledger_entry"]["source_type"] == "payment_collection_request"
    assert data["ledger_entry"]["source_id"] == request["id"]

    duplicate = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/approve",
        json={"review_note": "again"},
        headers=_auth(),
    )
    assert duplicate.status_code == 422


def test_approved_request_posts_one_idempotent_ledger_entry(client):
    request = _create_request(client)
    client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "REF123"},
        headers=_auth(),
    )
    approved = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/approve",
        json={"review_note": "matched wallet"},
        headers=_auth(),
    )
    assert approved.status_code == 200

    from app.radius.db.connection import db
    from app.radius.db.repos.payments_repo import PaymentCollectionLedgerRepository

    again = PaymentCollectionLedgerRepository().apply_paid_request(
        tenant_id=1,
        request_id=request["id"],
        actor="test",
    )
    assert again["id"] == approved.get_json()["data"]["request"]["ledger_entry_id"]

    count = db().execute(
        """
        SELECT COUNT(*) AS c FROM accounting_ledger_entries
        WHERE tenant_id=1 AND source_type='payment_collection_request' AND source_id=?
        """,
        (request["id"],),
    ).fetchone()["c"]
    assert count == 1


def test_reject_success_and_no_transaction(client):
    request = _create_request(client)
    client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "BADREF"},
        headers=_auth(),
    )
    rejected = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/reject",
        json={"review_note": "not found"},
        headers=_auth(),
    )
    assert rejected.status_code == 200
    assert rejected.get_json()["data"]["request"]["status"] == "rejected"

    from app.radius.db.connection import db

    count = db().execute(
        "SELECT COUNT(*) AS c FROM payment_collection_transactions WHERE payment_request_id=?",
        (request["id"],),
    ).fetchone()["c"]
    assert count == 0


def test_unpaid_request_cannot_apply_service(client):
    request = _create_request(client)
    response = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/apply-service",
        json={},
        headers=_auth(),
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_state"


def test_paid_request_service_apply_is_record_only_and_idempotent(client):
    request = _create_request(client)
    client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "REF123"},
        headers=_auth(),
    )
    client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/approve",
        json={"review_note": "matched wallet"},
        headers=_auth(),
    )
    applied = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/apply-service",
        json={},
        headers=_auth(),
    )
    assert applied.status_code == 200
    data = applied.get_json()["data"]
    assert data["request"]["service_apply_status"] == "applied"
    assert data["apply_attempt"]["status"] == "applied"
    assert '"live_radius_apply": false' in data["apply_attempt"]["result_json"]

    duplicate = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/apply-service",
        json={},
        headers=_auth(),
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()["data"]["apply_attempt"]["id"] == data["apply_attempt"]["id"]

    from app.radius.db.connection import db

    count = db().execute(
        """
        SELECT COUNT(*) AS c FROM payment_service_apply_attempts
        WHERE tenant_id=1 AND payment_request_id=? AND status='applied'
        """,
        (request["id"],),
    ).fetchone()["c"]
    assert count == 1


def test_service_apply_failure_is_recorded_without_ledger_rollback(client):
    request = _create_request(client)
    client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "REF123"},
        headers=_auth(),
    )
    approved = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/approve",
        json={"review_note": "matched wallet"},
        headers=_auth(),
    ).get_json()["data"]["request"]
    ledger_entry_id = approved["ledger_entry_id"]

    failed = client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/apply-service",
        json={"simulate_failure": True},
        headers=_auth(),
    )
    assert failed.status_code == 200
    data = failed.get_json()["data"]
    assert data["request"]["service_apply_status"] == "failed"
    assert data["request"]["ledger_entry_id"] == ledger_entry_id
    assert data["apply_attempt"]["status"] == "failed"

    from app.radius.db.connection import db

    ledger_count = db().execute(
        "SELECT COUNT(*) AS c FROM accounting_ledger_entries WHERE id=?",
        (ledger_entry_id,),
    ).fetchone()["c"]
    assert ledger_count == 1


def test_reconciliation_reports_paid_ledger_apply_and_expiry_findings(client):
    request = _create_request(client)
    client.post(
        f"/api/v1/payments/requests/{request['id']}/proofs",
        json={"reference_number": "REF123"},
        headers=_auth(),
    )
    client.post(
        f"/api/v1/admin/payments/requests/{request['id']}/approve",
        json={"review_note": "matched wallet"},
        headers=_auth(),
    )

    expired = _create_request(client)
    from app.radius.db.connection import transaction

    with transaction() as conn:
        conn.execute(
            """
            UPDATE payment_requests
            SET ledger_entry_id=NULL, ledger_applied_at=NULL
            WHERE id=?
            """,
            (request["id"],),
        )
        conn.execute(
            "UPDATE payment_requests SET expires_at='2000-01-01T00:00:00Z' WHERE id=?",
            (expired["id"],),
        )

    response = client.get("/api/v1/admin/payments/reconciliation", headers=_auth())
    assert response.status_code == 200
    report = response.get_json()["data"]["reconciliation"]
    assert report["counts"]["paid_without_ledger"] == 1
    assert report["counts"]["paid_not_applied"] == 1
    assert report["counts"]["expired_pending"] == 1
    assert report["counts"]["duplicate_provider_transactions"] == 0


def test_client_cannot_mark_paid_by_create_payload(client):
    request = _create_request(client)
    assert request["status"] == "pending"
