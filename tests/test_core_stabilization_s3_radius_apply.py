"""Core Stabilization S3 payment/loan RADIUS apply tests."""
from __future__ import annotations

from core_stabilization_helpers import AUTH, app, client, configured_plan, subscriber


def test_payment_and_loan_apply_to_radius_update_account_expiry(client):
    item = subscriber(client)
    pay = client.post(
        "/api/v1/payments",
        json={
            "username": item["username"],
            "plan_id": 1,
            "amount": 50,
            "apply_to_radius": True,
        },
        headers=AUTH,
    )
    assert pay.status_code == 201, pay.get_json()
    payment = pay.get_json()["data"]["payment"]
    assert payment["proportional_activation"]["earned_minutes"] == 14400
    assert payment["activation_result"]["applied_to_radius"] is True
    assert payment["radius_action_id"]

    after_payment = client.get(f"/api/v1/accounts/{item['username']}", headers=AUTH)
    assert after_payment.status_code == 200
    assert after_payment.get_json()["data"]["expire_at"]

    loan = client.post(
        "/api/v1/loans",
        json={"username": item["username"], "hours": 1, "apply_to_radius": True},
        headers=AUTH,
    )
    assert loan.status_code == 201, loan.get_json()
    payload = loan.get_json()["data"]["loan"]
    assert payload["activation_window"]["applied_to_radius"] is True
    assert payload["activation_result"]["new_expire_at"] >= payload["activation_result"]["old_expire_at"]


def test_payment_dry_run_does_not_change_radius_expiry(client):
    item = subscriber(client)
    before = client.get(f"/api/v1/accounts/{item['username']}", headers=AUTH).get_json()["data"]
    res = client.post(
        "/api/v1/payments",
        json={
            "username": item["username"],
            "plan_id": 1,
            "amount": 50,
            "apply_to_radius": True,
            "dry_run": True,
        },
        headers=AUTH,
    )
    assert res.status_code == 201, res.get_json()
    payment = res.get_json()["data"]["payment"]
    assert payment["activation_result"]["dry_run"] is True
    assert payment["activation_result"]["applied_to_radius"] is False
    after = client.get(f"/api/v1/accounts/{item['username']}", headers=AUTH).get_json()["data"]
    assert after["expire_at"] == before["expire_at"]
