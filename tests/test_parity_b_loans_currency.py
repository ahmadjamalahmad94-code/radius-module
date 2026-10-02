"""Parity-b (2026-10-02): loan value & currency follow the web.

* F11 (owner decision «أ»): a debt loan's value is price × duration, never a
  free-typed amount — a free amount with an explicit duration is re-priced.
* F1: a loan is always recorded in the system currency (the loans centre used to
  label a shekel price «JOD»).
* F2: the web settle form (no currency input) settles in the LOAN's currency.
* F3: a payment in the system currency cannot settle a foreign-currency loan 1:1.
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
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")  # license gate bypass (conftest)
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        with transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO access_plans(id, tenant_id, name, code, plan_type, "
                "service_type, duration_minutes, validity_days, speed_down_kbps, speed_up_kbps, "
                "price, currency, enabled, created_at) VALUES(1,1,'PB Plan','PBPLAN','time',"
                "'Hotspot',43200,30,4000,2000,150,'ILS',1,?)", (now_iso(),))
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def plan(app):
    from app.radius.db.connection import transaction
    with transaction() as conn:
        conn.execute("UPDATE access_plans SET price=150, duration_minutes=43200, "
                     "validity_days=30 WHERE tenant_id=1 AND id=1")


def _sub(client) -> str:
    u = "pb_" + secrets.token_hex(4)
    r = client.post("/api/v1/accounts", json={"username": u, "password": "pw1234",
                                              "plan_id": 1}, headers=AUTH)
    assert r.status_code == 201, r.get_json()
    return u


def _web_login(client) -> None:
    from uuid import uuid4
    from app.radius.db.repos import admins_repo
    username = f"pb_web_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username, password="pb-web-pass", full_name="PB Tester",
        is_super_admin=True,
        role_id=getattr(admins_repo.get_role_by_name("super_admin"), "id", None))
    admins_repo.set_designated_owners([username])
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": "pb-web-pass"})
    assert res.status_code in {302, 303}


def _sys_cur():
    from app.radius.core.system_config import default_currency
    return default_currency().upper()


def test_loan_currency_is_always_system_currency(client):
    u = _sub(client)
    other = "JOD" if _sys_cur() != "JOD" else "USD"
    r = client.post("/api/v1/loans", json={"username": u, "days": 2, "amount": 0,
                                           "price_from_days": True, "currency": other,
                                           "apply_to_radius": False}, headers=AUTH)
    assert r.status_code in (200, 201), r.get_json()
    loan = r.get_json()["data"]["loan"]
    assert loan["currency"] == _sys_cur()
    assert float(loan["amount"]) == pytest.approx(10.0)  # 150 / 30 × 2


def test_free_typed_debt_amount_is_repriced_from_days(client):
    u = _sub(client)
    r = client.post("/api/v1/loans", json={"username": u, "days": 3, "amount": 999,
                                           "apply_to_radius": False}, headers=AUTH)
    assert r.status_code in (200, 201), r.get_json()
    assert float(r.get_json()["data"]["loan"]["amount"]) == pytest.approx(15.0)


def test_free_loan_stays_free(client):
    u = _sub(client)
    r = client.post("/api/v1/loans", json={"username": u, "hours": 2, "amount": 0,
                                           "apply_to_radius": False}, headers=AUTH)
    assert r.status_code in (200, 201), r.get_json()
    assert float(r.get_json()["data"]["loan"]["amount"]) == 0.0


def _foreign_loan(app, u) -> int:
    """A legacy loan stored in a non-system currency (before F1)."""
    from app.radius.db.connection import transaction
    r = app.test_client().post("/api/v1/loans", json={"username": u, "days": 2,
                                                      "price_from_days": True,
                                                      "apply_to_radius": False}, headers=AUTH)
    lid = r.get_json()["data"]["loan"]["id"]
    other = "JOD" if _sys_cur() != "JOD" else "USD"
    with transaction() as conn:
        conn.execute("UPDATE loan_entries SET currency=? WHERE id=?", (other, lid))
    return lid


def test_web_settle_without_currency_uses_loan_currency(app, client):
    u = _sub(client)
    lid = _foreign_loan(app, u)
    _web_login(client)
    client.get(f"/admin/radius/users/{u}/finance")
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    r = client.post(f"/admin/radius/users/{u}/loans/{lid}/settle",
                    data={"_csrf_token": token, "amount": "1", "notes": "web"})
    assert r.status_code in (200, 302, 303)
    loan = client.get(f"/api/v1/loans/{lid}", headers=AUTH).get_json()["data"]["loan"]
    assert float(loan["outstanding"]) == pytest.approx(9.0), loan


def test_system_currency_payment_cannot_settle_foreign_loan(app, client):
    u = _sub(client)
    lid = _foreign_loan(app, u)
    r = client.post(f"/api/v1/accounts/{u}/payment", json={
        "amount": 30, "method": "cash",
        "loan_actions": [{"loan_id": lid, "action": "settle"}]}, headers=AUTH)
    assert r.status_code == 422, r.get_json()
    loan = client.get(f"/api/v1/loans/{lid}", headers=AUTH).get_json()["data"]["loan"]
    assert loan["status"] == "open" and float(loan["outstanding"]) == pytest.approx(10.0)
