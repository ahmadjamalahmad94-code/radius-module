from __future__ import annotations

import os

import pytest


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations

    run_pending_migrations()


def _wallet_service():
    from app.radius.services.business_os_finance import WalletService

    return WalletService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "finance_center.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        _run_pending_migrations()
    return flask_app


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
        res = client.get("/admin/radius/finance-center")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "المركز المالي" in html
    assert "الخزائن والمحافظ" in html
    assert "إجمالي الإيرادات" in html


def test_finance_wallets_route_lists_wallets_and_recent_transactions(app):
    with app.app_context():
        wallet = _wallet_service()().create_wallet(tenant_id=1, owner_type="manager", owner_id=44)
        _wallet_service()().credit(
            tenant_id=1,
            wallet_id=wallet["id"],
            amount="25.00",
            actor_type="admin",
            actor_id=1,
            reference_type="test",
        )

    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/finance-center?tab=wallets")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "مدير #44" in html
    assert "25 د.أ" in html
    assert "شحن" in html
    assert "خصم" in html


def test_finance_wallet_credit_action_writes_transaction(app):
    with app.app_context():
        wallet = _wallet_service()().create_wallet(tenant_id=1, owner_type="company")

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
        tx = _wallet_service()().list_transactions(tenant_id=1, wallet_id=wallet["id"])
    assert tx[0]["transaction_type"] == "credit"


def test_finance_legacy_urls_redirect_to_hub_and_ledger_has_no_delete(app):
    with app.test_client() as client:
        _auth_session(client)
        for path, expect in {
            "/admin/radius/finance": "tab=dashboard",
            "/admin/radius/finance/wallets": "tab=wallets",
            "/admin/radius/finance/revenue": "tab=revenue",
            "/admin/radius/finance/debts": "tab=loans_debts",
            "/admin/radius/finance/loans?status=settled": "status=settled",
            "/admin/radius/finance/ledger": "/finance/accounting",
        }.items():
            res = client.get(path, follow_redirects=False)
            assert res.status_code in {301, 302, 303}
            assert expect in res.headers.get("Location", "")

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
