from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


AUTH = {"Authorization": "Bearer business-os-api-token"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "business_os_api.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "business-os-api-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _data(response):
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def test_wallet_contract_creates_wallet_and_lists_transaction_history(client):
    created = client.post(
        "/api/v1/finance/wallets",
        headers=AUTH,
        json={"owner_type": "manager", "owner_id": 11, "currency": "JOD"},
    )
    assert created.status_code == 201
    wallet = _data(created)["wallet"]

    credit = client.post(
        f"/api/v1/finance/wallets/{wallet['id']}/credit",
        headers=AUTH,
        json={"amount": "20.00", "reference_type": "manual", "reference_id": 1},
    )
    assert credit.status_code == 201
    assert _data(credit)["wallet"]["balance"] == "20.00"

    debit = client.post(
        f"/api/v1/finance/wallets/{wallet['id']}/debit",
        headers=AUTH,
        json={"amount": "4.50", "reference_type": "manual", "reference_id": 2},
    )
    assert debit.status_code == 201
    assert _data(debit)["wallet"]["balance"] == "15.50"

    history = client.get(
        f"/api/v1/finance/wallets/{wallet['id']}/transactions",
        headers=AUTH,
    )
    assert history.status_code == 200
    items = _data(history)["items"]
    assert [item["transaction_type"] for item in items] == ["debit", "credit"]


def test_wallet_contract_rejects_invalid_amounts(client):
    created = client.post(
        "/api/v1/finance/wallets",
        headers=AUTH,
        json={"owner_type": "company", "owner_id": 1},
    )
    wallet = _data(created)["wallet"]

    invalid = client.post(
        f"/api/v1/finance/wallets/{wallet['id']}/credit",
        headers=AUTH,
        json={"amount": "0"},
    )
    assert invalid.status_code == 422
    assert invalid.get_json()["error"]["code"] == "validation_error"
    assert invalid.get_json()["error"]["message"] == "قيمة amount يجب أن تكون أكبر من صفر."

    missing = client.get("/api/v1/finance/wallets/999999999", headers=AUTH)
    assert missing.status_code == 404
    assert missing.get_json()["error"]["message"] == "المحفظة غير موجودة."


def test_ledger_contract_lists_entries_and_exposes_no_delete_semantics(client):
    created = client.post(
        "/api/v1/finance/ledger/corrections",
        headers=AUTH,
        json={
            "debit_account": "cash",
            "credit_account": "adjustments",
            "amount": "9.99",
            "currency": "JOD",
            "target_type": "operator",
        },
    )
    assert created.status_code == 201
    entry = _data(created)["entry"]

    listed = client.get("/api/v1/finance/ledger?entry_type=correction", headers=AUTH)
    assert listed.status_code == 200
    assert _data(listed)["items"][0]["id"] == entry["id"]

    delete_attempt = client.delete("/api/v1/finance/ledger", headers=AUTH)
    assert delete_attempt.status_code in {404, 405}


def test_events_contract_records_and_filters_by_category_and_severity(client):
    created = client.post(
        "/api/v1/events",
        headers=AUTH,
        json={
            "category": "security",
            "severity": "warning",
            "event_key": "operator.review",
            "message": "Operator review requested",
        },
    )
    assert created.status_code == 201
    event = _data(created)["event"]

    listed = client.get(
        "/api/v1/events?category=security&severity=warning",
        headers=AUTH,
    )
    assert listed.status_code == 200
    assert _data(listed)["items"][0]["id"] == event["id"]


def test_pricing_snapshots_and_revenue_summary_contracts(client):
    created = client.post(
        "/api/v1/pricing/snapshots",
        headers=AUTH,
        json={
            "reference_type": "subscription_quote",
            "reference_id": 123,
            "package_id": 7,
            "retail_price": "50.00",
            "wholesale_price": "35.00",
            "effective_price": "45.00",
            "discount_amount": "5.00",
        },
    )
    assert created.status_code == 201
    assert _data(created)["snapshot"]["effective_price"] == "45.00"

    snapshots = client.get(
        "/api/v1/pricing/snapshots?reference_type=subscription_quote",
        headers=AUTH,
    )
    assert snapshots.status_code == 200
    assert _data(snapshots)["count"] == 1

    revenue = client.get("/api/v1/finance/revenue", headers=AUTH)
    assert revenue.status_code == 200
    assert _data(revenue)["items"] == []

    summary = client.get("/api/v1/business/summary", headers=AUTH)
    assert summary.status_code == 200
    data = _data(summary)
    assert data["price_snapshots"] == 1
    assert data["events"] >= 1


def test_business_os_validation_messages_are_arabic(client):
    bad_package = client.get("/api/v1/pricing/snapshots?package_id=bad", headers=AUTH)
    assert bad_package.status_code == 422
    assert bad_package.get_json()["error"]["message"] == "معرّف الباقة يجب أن يكون رقمًا صحيحًا."

    bad_event = client.post(
        "/api/v1/events",
        headers=AUTH,
        json={"category": "bad", "severity": "info", "event_key": "x"},
    )
    assert bad_event.status_code == 422
    assert bad_event.get_json()["error"]["message"] == "تصنيف الحدث غير معروف."
