"""Customer roadmap API contract foundation tests."""
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


def _count(table: str) -> int:
    from app.radius.db.connection import db
    return int(db().execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def test_contract_endpoint_requires_auth(client):
    res = client.get("/api/v1/loans")
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_recycle_bin_restore_returns_real_not_found_shape(client):
    res = client.post("/api/v1/recycle-bin/subscribers/1/restore", json={}, headers=AUTH)
    assert res.status_code == 404
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


def test_contract_routes_are_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    expected = {
        "/api/v1/loans",
        "/api/v1/recycle-bin",
        "/api/v1/ledger",
        "/api/v1/payments",
        "/api/v1/distributors",
        "/api/v1/reports/sales",
        "/api/v1/bandwidth-schedules",
        "/api/v1/print-templates",
        "/api/v1/backups/status",
        "/api/v1/cards/check",
    }
    assert expected.issubset(routes)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/loans"),
    ("POST", "/api/v1/loans"),
    ("GET", "/api/v1/recycle-bin"),
    ("POST", "/api/v1/recycle-bin/subscribers/1/restore"),
    ("GET", "/api/v1/ledger"),
    ("POST", "/api/v1/ledger/void"),
    ("GET", "/api/v1/payments"),
    ("POST", "/api/v1/payments"),
    ("GET", "/api/v1/distributors"),
    ("POST", "/api/v1/distributors/1/settle"),
    ("GET", "/api/v1/reports/sales"),
    ("GET", "/api/v1/reports/card-sales"),
    ("GET", "/api/v1/bandwidth-schedules"),
    ("POST", "/api/v1/bandwidth-schedules/1/apply"),
    ("GET", "/api/v1/print-templates"),
    ("POST", "/api/v1/print-templates/1/render"),
    ("GET", "/api/v1/backups/status"),
    ("POST", "/api/v1/backups/run"),
])
def test_contract_endpoints_do_not_500(client, method, path):
    res = client.open(path, method=method, json={}, headers=AUTH)
    assert res.status_code in {200, 201, 404, 422, 501}, (
        path, res.status_code, res.get_data(as_text=True)
    )
    body = res.get_json()
    assert body["ok"] is (res.status_code in {200, 201})
    if res.status_code == 501:
        assert body["error"]["code"] == "not_implemented"


def test_contract_mutations_do_not_mutate_core_tables(client):
    before = {
        "subscribers": _count("subscribers"),
        "cards": _count("cards"),
        "card_batches": _count("card_batches"),
        "audit_log": _count("audit_log"),
        "accounting_ledger_entries": _count("accounting_ledger_entries"),
        "payment_transactions": _count("payment_transactions"),
        "loan_entries": _count("loan_entries"),
    }
    for path in (
        "/api/v1/loans",
        "/api/v1/payments",
        "/api/v1/ledger/void",
        "/api/v1/distributors/1/settle",
        "/api/v1/bandwidth-schedules/999999/apply",
    ):
        res = client.post(path, json={"probe": True}, headers=AUTH)
        assert res.status_code in {404, 422, 501}
    after = {table: _count(table) for table in before}
    assert after == before


def test_card_checker_still_works_and_does_not_leak_password(client):
    prefix = "ct" + secrets.token_hex(4)
    created = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 1, "username_prefix": prefix},
        headers=AUTH,
    )
    assert created.status_code == 201, created.get_json()
    card = created.get_json()["data"]["cards"][0]

    checked = client.get(
        "/api/v1/cards/check",
        query_string={"query": card["username"]},
        headers=AUTH,
    )
    assert checked.status_code == 200, checked.get_json()
    payload = checked.get_json()["data"]["card"]
    assert payload["exists"] is True
    assert payload["has_password"] is True
    assert "password" not in payload
    assert payload["status"] in {"available", "active", "expired", "revoked"}
