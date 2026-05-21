"""Core Stabilization S5 ledger report tests."""
from __future__ import annotations

from core_stabilization_helpers import AUTH, app, client, configured_plan, subscriber


def test_reports_are_ledger_based_and_voids_reverse_sales(client):
    item = subscriber(client)
    payment = client.post(
        "/api/v1/payments",
        json={"username": item["username"], "plan_id": 1, "amount": 30},
        headers=AUTH,
    ).get_json()["data"]["payment"]
    voided = client.post(
        "/api/v1/ledger/void",
        json={"entry_id": payment["ledger_entry_id"], "reason": "qa reversal"},
        headers=AUTH,
    )
    assert voided.status_code == 201, voided.get_json()

    payments = client.get("/api/v1/reports/payments", headers=AUTH)
    assert payments.status_code == 200, payments.get_json()
    rows = payments.get_json()["data"]["items"]
    row = next(record for record in rows if record["username"] == item["username"])
    assert float(row["total"] or 0) == 0

    profit = client.get("/api/v1/reports/profit-loss", headers=AUTH)
    assert profit.status_code == 200, profit.get_json()
    assert profit.get_json()["data"]["items"][0]["source"] == "accounting_ledger_entries"


def test_financial_report_snapshots_freeze_current_report(client):
    item = subscriber(client)
    client.post(
        "/api/v1/payments",
        json={"username": item["username"], "plan_id": 1, "amount": 17},
        headers=AUTH,
    )

    created = client.post(
        "/api/v1/reports/snapshots",
        json={
            "report_type": "subscriber_payments",
            "parameters": {"reason": "qa snapshot"},
        },
        headers=AUTH,
    )
    assert created.status_code == 201, created.get_json()
    snapshot = created.get_json()["data"]["snapshot"]
    assert snapshot["report_type"] == "subscriber_payments"
    assert snapshot["parameters"]["reason"] == "qa snapshot"
    assert snapshot["result"]["count"] >= 1
    assert any(row["username"] == item["username"] for row in snapshot["result"]["items"])

    listed = client.get("/api/v1/reports/snapshots?report_type=subscriber_payments", headers=AUTH)
    assert listed.status_code == 200, listed.get_json()
    assert listed.get_json()["data"]["items"][0]["id"] == snapshot["id"]

    fetched = client.get(f"/api/v1/reports/snapshots/{snapshot['id']}", headers=AUTH)
    assert fetched.status_code == 200, fetched.get_json()
    assert fetched.get_json()["data"]["snapshot"]["id"] == snapshot["id"]


def test_financial_report_csv_export_is_real(client):
    item = subscriber(client)
    client.post(
        "/api/v1/payments",
        json={"username": item["username"], "plan_id": 1, "amount": 19},
        headers=AUTH,
    )
    export = client.get("/api/v1/reports/payments/export.csv", headers=AUTH)
    assert export.status_code == 200
    assert export.headers["Content-Type"].startswith("text/csv")
    text = export.get_data(as_text=True)
    assert "username" in text
    assert item["username"] in text
    assert "19" in text
