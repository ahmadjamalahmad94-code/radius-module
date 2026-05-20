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
