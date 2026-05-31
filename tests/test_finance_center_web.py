from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.business_os_finance import WalletService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "finance_center.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "finance_admin"
        sess["admin_name"] = "Finance Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "finance-csrf"


def test_finance_center_dashboard_route_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/finance")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "المركز المالي" in html
    assert "الخزائن والمحافظ" in html
    assert "إجمالي الإيرادات" in html


def test_finance_wallets_route_lists_wallets_and_recent_transactions(app):
    with app.app_context():
        wallet = WalletService().create_wallet(tenant_id=1, owner_type="manager", owner_id=44)
        WalletService().credit(
            tenant_id=1,
            wallet_id=wallet["id"],
            amount="25.00",
            actor_type="admin",
            actor_id=1,
            reference_type="test",
        )

    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/finance/wallets")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "manager #44" in html
    assert "25.00" in html
    assert "شحن" in html
    assert "خصم" in html


def test_finance_wallet_credit_action_writes_transaction(app):
    with app.app_context():
        wallet = WalletService().create_wallet(tenant_id=1, owner_type="company")

    with app.test_client() as client:
        _auth_session(client)
        res = client.post(
            f"/admin/radius/finance/wallets/{wallet['id']}/credit",
            data={"_csrf_token": "finance-csrf", "amount": "11.25"},
            follow_redirects=True,
        )
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "11.25" in html
    with app.app_context():
        tx = WalletService().list_transactions(tenant_id=1, wallet_id=wallet["id"])
    assert tx[0]["transaction_type"] == "credit"


def test_finance_section_routes_render_and_ledger_has_no_delete(app):
    with app.test_client() as client:
        _auth_session(client)
        for path, marker in {
            "/admin/radius/finance/revenue": "الإيرادات",
            "/admin/radius/finance/debts": "الديون",
            "/admin/radius/finance/loans": "السلف",
            "/admin/radius/finance/ledger": "ledger-list",
        }.items():
            res = client.get(path)
            assert res.status_code == 200
            html = res.get_data(as_text=True)
            assert marker in html
            assert '"status": "placeholder"' not in html.lower()
            assert "سيتم ربط" not in html

        delete_attempt = client.delete("/admin/radius/finance/ledger")
        assert delete_attempt.status_code >= 400


def test_report_archive_page_uses_arabic_visible_copy(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/reports/archive")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "أرشيف التقارير" in html
    assert "نوع الأرشفة" in html
    assert "اللقطات المحفوظة" in html
    assert "Report Archives" not in html
    assert "Archive type" not in html
    assert "Existing archives" not in html
