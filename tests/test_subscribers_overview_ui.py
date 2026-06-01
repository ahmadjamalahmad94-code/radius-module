"""Subscribers Overview — aggregation helpers + page UI.

Mirrors the seeding/auth pattern of test_accounting_loans_foundation.py.
The page is a read-only snapshot (see SERVICES_COOKBOOK.md §20): it aggregates
loans / activations / data-usage / outstanding-debt for the المشتركون section,
monthly + yearly only, and links every detail out to the Finance section.
"""
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
    return "sov_" + secrets.token_hex(5)


def _create_subscriber(client, *, plan_id: int = 1) -> dict:
    username = _username()
    res = client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw1234", "plan_id": plan_id},
        headers=AUTH,
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def _seed_payment_and_loan(client) -> dict:
    sub = _create_subscriber(client)
    pay = client.post(
        "/api/v1/payments",
        json={"username": sub["username"], "plan_id": 1, "amount": 50},
        headers=AUTH,
    )
    assert pay.status_code == 201, pay.get_json()
    loan = client.post(
        "/api/v1/loans",
        json={"username": sub["username"], "hours": 1, "amount": 8, "reason": "qa"},
        headers=AUTH,
    )
    assert loan.status_code == 201, loan.get_json()
    return sub


# ───────────────────────── aggregation helpers ─────────────────────────


def test_summary_helpers_bucket_real_records(client):
    sub = _seed_payment_and_loan(client)
    from app.radius.db.repos import accounting_repo as ar

    for grain in ("monthly", "yearly"):
        sales = ar.sales_summary(1, grain=grain)
        loans = ar.loans_summary(1, grain=grain)
        acts = ar.activation_summary(1, grain=grain)
        data = ar.data_usage_summary(1, grain=grain)
        assert isinstance(sales, list)
        assert isinstance(loans, list) and loans, "expected at least one loan bucket"
        assert isinstance(acts, list) and acts, "expected at least one activation bucket"
        assert isinstance(data, list)

        # bucket key length proves the grain (YYYY-MM=7 vs YYYY=4)
        assert len(loans[0]["period"]) == (7 if grain == "monthly" else 4)
        assert len(acts[0]["period"]) == (7 if grain == "monthly" else 4)

        # the loan we created (amount 8, 60 min) is counted
        assert sum(r["total"] for r in loans) >= 8
        assert sum(r["minutes"] for r in loans) >= 60
        assert sum(r["still_open"] for r in loans) >= 1

        # the payment granted time → shows as an activation
        assert sum(r["count"] for r in acts) >= 1
        assert sum(r["minutes"] for r in acts) >= 1


def test_outstanding_summary_is_point_in_time(client):
    sub = _seed_payment_and_loan(client)
    from app.radius.db.repos import accounting_repo as ar

    out = ar.outstanding_summary(1)
    # «شو ضل» = open loans + negative balances (as-of-now)
    assert out["open_loans_total"] >= 8
    assert out["open_loans_count"] >= 1
    assert out["open_loans_minutes"] >= 60
    assert out["outstanding_total"] >= out["open_loans_total"]
    for key in ("balance_owed", "balance_owed_count", "balance_credit", "balance_credit_count"):
        assert key in out


def test_top_debtors_lists_open_loan_holders(client):
    sub = _seed_payment_and_loan(client)
    from app.radius.db.repos import accounting_repo as ar

    debtors = ar.top_debtors(1, limit=10)
    assert isinstance(debtors, list)
    match = [d for d in debtors if d["username"] == sub["username"]]
    assert match, "subscriber with an open loan should appear in top_debtors"
    assert match[0]["open_loans_total"] >= 8
    assert match[0]["subscriber_id"]
