"""Accounting + loans foundation tests."""
from __future__ import annotations

import secrets

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def configured_plan(app):
    from app.radius.db.connection import transaction

    with transaction() as conn:
        conn.execute(
            """
            UPDATE access_plans
            SET price = 150, duration_minutes = 43200, validity_days = 30
            WHERE tenant_id = 1 AND id = 1
            """
        )


def _username() -> str:
    return "acct_" + secrets.token_hex(5)


def _create_subscriber(client, *, plan_id: int = 1) -> dict:
    username = _username()
    res = client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw1234", "plan_id": plan_id},
        headers=AUTH,
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def _delete_paths(app) -> set[str]:
    return {
        rule.rule
        for rule in app.url_map.iter_rules()
        if "DELETE" in rule.methods and rule.endpoint.startswith("api.v1.")
    }


def test_payment_posts_ledger_and_calculates_proportional_duration(client):
    subscriber = _create_subscriber(client)
    res = client.post(
        "/api/v1/payments",
        json={
            "username": subscriber["username"],
            "plan_id": 1,
            "amount": 50,
            "method": "cash",
            "rounding_mode": "floor",
        },
        headers=AUTH,
    )
    assert res.status_code == 201, res.get_json()
    payment = res.get_json()["data"]["payment"]
    assert payment["amount"] == 50
    assert payment["plan_price"] == 150
    assert payment["effective_price"] == 150
    assert payment["earned_minutes"] == 14400
    assert payment["proportional_activation"] == {
        "base_minutes": 43200,
        "earned_minutes": 14400,
        "rounding_mode": "floor",
        "applied_to_radius": False,
    }
    assert payment["ledger_entry_id"]

    ledger = client.get("/api/v1/ledger?entry_type=payment", headers=AUTH)
    assert ledger.status_code == 200
    assert any(
        item["id"] == payment["ledger_entry_id"]
        for item in ledger.get_json()["data"]["items"]
    )


def test_custom_price_and_discount_are_tracked(client):
    subscriber = _create_subscriber(client)
    res = client.post(
        "/api/v1/payments",
        json={
            "username": subscriber["username"],
            "plan_id": 1,
            "amount": 60,
            "custom_price": 120,
            "discount_amount": 20,
            "discount_reason": "loyal customer",
            "rounding_mode": "floor",
        },
        headers=AUTH,
    )
    assert res.status_code == 201, res.get_json()
    payment = res.get_json()["data"]["payment"]
    assert payment["custom_price"] == 120
    assert payment["discount_amount"] == 20
    assert payment["discount_reason"] == "loyal customer"
    assert payment["effective_price"] == 100
    assert payment["earned_minutes"] == 25920


def test_loan_lifecycle_and_settlement(client, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_MAX_LOAN_HOURS", "24")
    subscriber = _create_subscriber(client)
    create = client.post(
        "/api/v1/loans",
        json={
            "username": subscriber["username"],
            "hours": 2,
            "amount": 10,
            "reason": "temporary support activation",
        },
        headers=AUTH,
    )
    assert create.status_code == 201, create.get_json()
    loan = create.get_json()["data"]["loan"]
    assert loan["status"] == "open"
    assert loan["duration_minutes"] == 120
    assert loan["amount"] == 10
    assert loan["ledger_entry_id"]
    assert loan["activation_window"]["applied_to_radius"] is False

    settle = client.post(
        f"/api/v1/loans/{loan['id']}/settle",
        json={"amount": 10, "method": "cash", "notes": "paid"},
        headers=AUTH,
    )
    assert settle.status_code == 201, settle.get_json()
    settlement = settle.get_json()["data"]["settlement"]
    assert settlement["loan_id"] == loan["id"]
    assert settlement["ledger_entry_id"]

    get_loan = client.get(f"/api/v1/loans/{loan['id']}", headers=AUTH)
    assert get_loan.status_code == 200
    assert get_loan.get_json()["data"]["loan"]["status"] == "settled"


def test_loan_limit_is_enforced(client, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_MAX_LOAN_HOURS", "1")
    subscriber = _create_subscriber(client)
    res = client.post(
        "/api/v1/loans",
        json={"username": subscriber["username"], "hours": 2, "reason": "too long"},
        headers=AUTH,
    )
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "validation_error"


def test_ledger_void_is_append_only_and_no_financial_delete_routes_exist(client, app):
    subscriber = _create_subscriber(client)
    payment = client.post(
        "/api/v1/payments",
        json={"username": subscriber["username"], "plan_id": 1, "amount": 20},
        headers=AUTH,
    ).get_json()["data"]["payment"]

    voided = client.post(
        "/api/v1/ledger/void",
        json={"entry_id": payment["ledger_entry_id"], "reason": "test correction"},
        headers=AUTH,
    )
    assert voided.status_code == 201, voided.get_json()
    void_entry = voided.get_json()["data"]["entry"]
    assert void_entry["entry_type"] == "void"
    assert void_entry["reversal_of_entry_id"] == payment["ledger_entry_id"]
    assert void_entry["amount"] == -20

    ledger = client.get("/api/v1/ledger", headers=AUTH).get_json()["data"]["items"]
    ids = {item["id"] for item in ledger}
    assert payment["ledger_entry_id"] in ids
    assert void_entry["id"] in ids

    delete_paths = _delete_paths(app)
    assert "/api/v1/payments/<int:payment_id>" not in delete_paths
    assert "/api/v1/loans/<int:loan_id>" not in delete_paths
    assert "/api/v1/ledger/<int:entry_id>" not in delete_paths


def test_payment_void_is_real_append_only_reversal(client):
    subscriber = _create_subscriber(client)
    payment = client.post(
        "/api/v1/payments",
        json={"username": subscriber["username"], "amount": 30},
        headers=AUTH,
    ).get_json()["data"]["payment"]

    voided = client.post(
        f"/api/v1/payments/{payment['id']}/void",
        json={"reason": "operator correction"},
        headers=AUTH,
    )
    assert voided.status_code == 201, voided.get_json()
    data = voided.get_json()["data"]
    assert data["payment"]["status"] == "voided"
    assert data["entry"]["entry_type"] == "void"
    assert data["entry"]["source_type"] == "payment_void"
    assert data["entry"]["source_id"] == payment["id"]
    assert data["entry"]["reversal_of_entry_id"] == payment["ledger_entry_id"]
    assert data["entry"]["amount"] == -30

    listed = client.get("/api/v1/payments", headers=AUTH).get_json()["data"]["items"]
    assert any(item["id"] == payment["id"] and item["status"] == "voided" for item in listed)


def test_foundational_reports_aggregate_real_records(client):
    subscriber = _create_subscriber(client)
    client.post(
        "/api/v1/payments",
        json={"username": subscriber["username"], "plan_id": 1, "amount": 30},
        headers=AUTH,
    )
    client.post(
        "/api/v1/loans",
        json={"username": subscriber["username"], "hours": 1, "amount": 5, "reason": "qa"},
        headers=AUTH,
    )

    for path in (
        "/api/v1/reports/sales/daily",
        "/api/v1/reports/sales/monthly",
        "/api/v1/reports/sales/yearly",
        "/api/v1/reports/payments",
        "/api/v1/reports/loans",
        "/api/v1/reports/profit-loss",
    ):
        res = client.get(path, headers=AUTH)
        assert res.status_code == 200, (path, res.get_json())
        assert res.get_json()["data"]["count"] >= 1
