"""Web UI smoke tests for payments, loans, and ledger screens."""
from __future__ import annotations

import secrets
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO access_plans(
                    id, tenant_id, name, code, plan_type, service_type,
                    duration_minutes, validity_days, speed_down_kbps,
                    speed_up_kbps, price, currency, enabled, created_at
                )
                VALUES(1,1,'Accounting Web UI Test Plan','ACCTWEB','time','Hotspot',
                       43200,30,4000,2000,150,'JOD',1,?)
                """,
                (now_iso(),),
            )
            conn.execute(
                """
                UPDATE access_plans
                SET price = 150, duration_minutes = 43200, validity_days = 30
                WHERE tenant_id = 1 AND id = 1
                """
            )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"acct_web_{uuid4().hex[:10]}"
    password = "acct-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Accounting Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _auth_headers(client) -> dict:
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


def _subscriber(client) -> dict:
    username = "acctui_" + secrets.token_hex(5)
    res = client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw1234", "plan_id": 1},
        headers=_auth_headers(client),
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def test_accounting_web_routes_are_login_guarded(client):
    res = client.get("/admin/radius/finance/ledger", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_subscriber_finance_payment_loan_settlement_and_ledger_void(client):
    _web_login(client)
    sub = _subscriber(client)
    finance_url = f"/admin/radius/users/{sub['username']}/finance"
    token = _csrf(client, finance_url)

    payment = client.post(
        f"/admin/radius/users/{sub['username']}/payments",
        data={
            "_csrf_token": token,
            "amount": "50",
            "currency": "JOD",
            "method": "cash",
            "rounding_mode": "floor",
            "apply_to_radius": "1",
            "dry_run": "1",
            "notes": "web payment smoke",
        },
        follow_redirects=True,
    )
    assert payment.status_code == 200
    assert "web payment smoke" in payment.get_data(as_text=True)

    loan = client.post(
        f"/admin/radius/users/{sub['username']}/loans",
        data={
            "_csrf_token": token,
            "hours": "2",
            "amount": "10",
            "currency": "JOD",
            "reason": "temporary support",
            "dry_run": "1",
        },
        follow_redirects=True,
    )
    assert loan.status_code == 200
    assert "temporary support" in loan.get_data(as_text=True)

    open_loans = client.get(
        f"/api/v1/loans?subscriber_id={sub['id']}&status=open",
        headers=_auth_headers(client),
    ).get_json()["data"]["items"]
    assert open_loans
    settled = client.post(
        f"/admin/radius/users/{sub['username']}/loans/{open_loans[0]['id']}/settle",
        data={"_csrf_token": token, "amount": "10", "notes": "settled from web"},
        follow_redirects=True,
    )
    assert settled.status_code == 200
    assert "settled" in settled.get_data(as_text=True)

    ledger_page = client.get(f"/admin/radius/finance/ledger?subscriber_id={sub['id']}")
    assert ledger_page.status_code == 200
    ledger_html = ledger_page.get_data(as_text=True)
    assert "web payment smoke" in ledger_html
    assert "temporary support" in ledger_html

    entries = client.get(
        f"/api/v1/ledger?subscriber_id={sub['id']}&entry_type=payment",
        headers=_auth_headers(client),
    ).get_json()["data"]["items"]
    assert entries
    voided = client.post(
        "/admin/radius/finance/ledger/void",
        data={"_csrf_token": token, "entry_id": entries[0]["id"], "reason": "web correction"},
        follow_redirects=True,
    )
    assert voided.status_code == 200
    assert "web correction" in client.get(
        f"/admin/radius/finance/ledger?subscriber_id={sub['id']}",
    ).get_data(as_text=True)


def test_financial_reports_page_reads_ledger_reports(client):
    _web_login(client)
    sub = _subscriber(client)
    token = _csrf(client, f"/admin/radius/users/{sub['username']}/finance")
    created = client.post(
        f"/admin/radius/users/{sub['username']}/payments",
        data={
            "_csrf_token": token,
            "amount": "25",
            "currency": "JOD",
            "method": "cash",
            "notes": "report smoke",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200

    reports = client.get("/admin/radius/finance/reports?type=subscriber_payments")
    assert reports.status_code == 200
    html = reports.get_data(as_text=True)
    assert "دفعات المستفيدين" in html
    assert sub["username"] in html
    assert "25" in html

    snapshot = client.post(
        "/admin/radius/finance/reports/snapshot",
        data={"_csrf_token": token, "report_type": "subscriber_payments"},
        follow_redirects=True,
    )
    assert snapshot.status_code == 200
    snapshot_html = snapshot.get_data(as_text=True)
    assert "تم حفظ لقطة ثابتة للتقرير" in snapshot_html
    assert "آخر اللقطات الثابتة" in snapshot_html

    csv_export = client.get("/admin/radius/finance/reports/export.csv?type=subscriber_payments")
    assert csv_export.status_code == 200
    assert csv_export.headers["Content-Type"].startswith("text/csv")
    assert sub["username"] in csv_export.get_data(as_text=True)

    xlsx_export = client.get("/admin/radius/finance/reports/export.xlsx?type=subscriber_payments")
    assert xlsx_export.status_code == 200
    assert xlsx_export.headers["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert xlsx_export.data[:2] == b"PK"
