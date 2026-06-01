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


def test_free_loan_capped_but_debt_loan_may_exceed_free_cap(client, monkeypatch):
    """The duration cap is for FREE loans (temporary access). A DEBT loan
    (price_from_days / recorded value) is recorded credit, so it's allowed to
    span beyond the free cap — bounded only by the generous debt sanity limit."""
    monkeypatch.setenv("HOBERADIUS_MAX_LOAN_HOURS", "72")  # free cap = 3 days
    subscriber = _create_subscriber(client)

    # 5-day DEBT loan (priced from days) is ALLOWED even though it exceeds 72h.
    debt = client.post(
        "/api/v1/loans",
        json={"username": subscriber["username"], "days": 5, "price_from_days": True,
              "reason": "5-day credit"},
        headers=AUTH,
    )
    assert debt.status_code == 201, debt.get_json()
    assert debt.get_json()["data"]["loan"]["duration_minutes"] == 5 * 24 * 60

    # The SAME 5-day span as a FREE loan is rejected, in Arabic, with guidance.
    free = client.post(
        "/api/v1/loans",
        json={"username": subscriber["username"], "days": 5, "reason": "5-day free"},
        headers=AUTH,
    )
    assert free.status_code == 422
    assert free.get_json()["error"]["code"] == "validation_error"
    assert "السلفة المجانية" in free.get_json()["error"]["message"]


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


def test_calculate_proportional_amount_inverse_and_rounding():
    from app.radius.services.accounting import (
        calculate_proportional_amount,
        calculate_proportional_minutes,
    )

    # 100 over a 30-day plan (43200 min), 7 days (10080 min) -> 23.33 (2 decimals)
    assert calculate_proportional_amount(minutes=10080, plan_price=100, base_minutes=43200) == 23.33
    # clean division -> exact
    assert calculate_proportional_amount(minutes=4320, plan_price=150, base_minutes=43200) == 15.0
    # guards
    assert calculate_proportional_amount(minutes=0, plan_price=100, base_minutes=43200) == 0.0
    assert calculate_proportional_amount(minutes=4320, plan_price=0, base_minutes=43200) == 0.0
    # round-trips with the existing minutes helper (floor) at a clean point
    mins = calculate_proportional_minutes(amount_paid=15.0, plan_price=150, base_minutes=43200)
    assert mins == 4320


def test_loan_price_from_days_derives_amount_from_effective_price(client):
    # configured_plan fixture pins plan 1 = price 150, duration 43200 min (30 days)
    subscriber = _create_subscriber(client)
    res = client.post(
        "/api/v1/loans",
        json={
            "username": subscriber["username"],
            "days": 3,
            "price_from_days": True,
            "reason": "auto-priced loan",
        },
        headers=AUTH,
    )
    assert res.status_code == 201, res.get_json()
    loan = res.get_json()["data"]["loan"]
    assert loan["duration_minutes"] == 3 * 24 * 60
    # 150 × (4320 / 43200) = 15.00 — derived from the effective price, not typed
    assert abs(float(loan["amount"]) - 15.0) < 0.001


def test_writeoff_loan_voids_and_posts_reversing_credit(client):
    subscriber = _create_subscriber(client)
    loan = client.post(
        "/api/v1/loans",
        json={"username": subscriber["username"], "days": 2, "price_from_days": True, "reason": "wo"},
        headers=AUTH,
    ).get_json()["data"]["loan"]

    from app.radius.db.connection import db
    from app.radius.db.repos import accounting_repo

    full = accounting_repo.get_loan(1, loan["id"])
    out = accounting_repo.writeoff_loan(
        tenant_id=1, loan=full, currency=full["currency"],
        created_by="tester", notes="forgive",
    )
    assert out["status"] == "voided"
    # a reversing CREDIT ledger entry nets the original loan debit
    wo = db().execute(
        "SELECT * FROM accounting_ledger_entries WHERE tenant_id=1 "
        "AND entry_type='writeoff' AND related_id=?",
        (loan["id"],),
    ).fetchone()
    assert wo is not None
    assert wo["direction"] == "credit"
    assert abs(float(wo["amount"]) - float(full["amount"])) < 0.001


def test_payment_loan_settled_total_deducts_from_time_basis(client):
    # plan 1 = price 150 / 43200 min (30 days)
    subscriber = _create_subscriber(client)
    # baseline: a 30 payment with no settlement buys 30/150 × 43200 = 8640 min
    base = client.post(
        "/api/v1/payments",
        json={"username": subscriber["username"], "amount": 30},
        headers=AUTH,
    )
    assert base.status_code == 201, base.get_json()
    assert base.get_json()["data"]["payment"]["earned_minutes"] == 8640

    # with loan_settled_total=10, only (30-10)=20 buys time → 20/150 × 43200 = 5760
    settled = client.post(
        "/api/v1/payments",
        json={"username": subscriber["username"], "amount": 30, "loan_settled_total": 10},
        headers=AUTH,
    )
    assert settled.status_code == 201, settled.get_json()
    payment = settled.get_json()["data"]["payment"]
    assert payment["earned_minutes"] == 5760  # time-basis reduced by the settled 10
    assert payment["amount"] == 30  # full amount still recorded as income


def test_settle_preview_is_read_only_then_resolve_settles(client, app):
    # The payment route previews the settle total (read-only) BEFORE creating the
    # payment, and only settles loans AFTER — so a failed payment never orphans loans.
    subscriber = _create_subscriber(client)
    loan = client.post(
        "/api/v1/loans",
        json={"username": subscriber["username"], "days": 2, "price_from_days": True},
        headers=AUTH,
    ).get_json()["data"]["loan"]
    loan_amount = float(loan["amount"])
    actions = [{"loan_id": loan["id"], "action": "settle"}]

    from app.radius.db.repos import accounting_repo
    from app.radius.services.accounting import service_from_context

    with app.test_request_context("/", headers=AUTH):
        svc = service_from_context()
        # preview returns the amount WITHOUT settling
        preview = svc.settle_preview_total(actions)
        assert abs(preview - loan_amount) < 0.01
        assert accounting_repo.get_loan(1, loan["id"])["status"] == "open"  # untouched
        # resolve actually settles
        resolved = svc.resolve_loan_actions(actions, actor="tester")
        assert abs(float(resolved["settled_total"]) - loan_amount) < 0.01
        assert accounting_repo.get_loan(1, loan["id"])["status"] == "settled"
