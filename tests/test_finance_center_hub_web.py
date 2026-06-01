"""Tests for Hub 4 — المركز المالي الموحد."""
from __future__ import annotations

import os
import html
import re

import pytest


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations

    run_pending_migrations()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "finance_center_hub.db")
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


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "finance_hub_admin"
        sess["admin_name"] = "Finance Hub Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "finance-hub-csrf"


def _counts(app):
    with app.app_context():
        from app.radius.db.connection import db

        conn = db()
        return {
            "wallets": conn.execute("SELECT COUNT(*) AS c FROM wallets").fetchone()["c"],
            "wallet_transactions": conn.execute(
                "SELECT COUNT(*) AS c FROM wallet_transactions"
            ).fetchone()["c"],
            "revenue_records": conn.execute(
                "SELECT COUNT(*) AS c FROM revenue_records"
            ).fetchone()["c"],
            "loan_entries": conn.execute(
                "SELECT COUNT(*) AS c FROM loan_entries"
            ).fetchone()["c"],
        }


_HUB = "/admin/radius/finance-center"


def _visible_text(markup: str) -> str:
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def test_finance_center_hub_renders_all_tabs(app):
    with app.test_client() as client:
        _auth(client)
        pages = {
            "dashboard": "قاعدة الأمان المالي",
            "wallets": "إنشاء محفظة",
            "revenue": "سجلات الإيرادات",
            "loans_debts": "الذمم المفتوحة",
        }
        for tab, marker in pages.items():
            res = client.get(f"{_HUB}?tab={tab}")
            assert res.status_code == 200
            html = res.get_data(as_text=True)
            text = _visible_text(html)
            assert "المركز المالي" in html
            assert marker in html
            assert "قريبًا" not in text
            assert "سيتم ربط" not in text


def test_legacy_finance_urls_redirect_to_center_hub(app):
    with app.test_client() as client:
        _auth(client)
        cases = {
            "/admin/radius/finance": "tab=dashboard",
            "/admin/radius/finance/wallets": "tab=wallets",
            "/admin/radius/finance/revenue": "tab=revenue",
            "/admin/radius/finance/debts": "status=open",
            "/admin/radius/finance/loans?status=settled": "status=settled",
        }
        for old, expect in cases.items():
            res = client.get(old, follow_redirects=False)
            assert res.status_code in {301, 302, 303}
            loc = res.headers.get("Location", "")
            assert "/finance-center" in loc
            assert expect in loc


def test_wallet_actions_stay_on_original_posts_and_return_to_hub(app):
    with app.app_context():
        from app.radius.services.business_os_finance import WalletService

        wallet = WalletService().create_wallet(tenant_id=1, owner_type="company")

    with app.test_client() as client:
        _auth(client)
        html = client.get(f"{_HUB}?tab=wallets").get_data(as_text=True)
        assert f'/finance/wallets/{wallet["id"]}/credit' in html
        res = client.post(
            f"/admin/radius/finance/wallets/{wallet['id']}/credit",
            data={"_csrf_token": "finance-hub-csrf", "amount": "12.50"},
            follow_redirects=False,
        )
        assert res.status_code in {301, 302, 303}
        assert "/finance-center?tab=wallets" in res.headers.get("Location", "")

    with app.app_context():
        from app.radius.services.business_os_finance import WalletService

        transactions = WalletService().list_transactions(tenant_id=1, wallet_id=wallet["id"])
    assert transactions[0]["transaction_type"] == "credit"


def test_finance_center_hub_link_is_collapsed_in_sidebar(app):
    with app.test_client() as client:
        _auth(client)
        response = client.get("/admin/radius/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "/finance-center" in html
    assert "المركز المالي" in html
    assert 'href="/admin/radius/finance/wallets"' not in html
    assert 'href="/admin/radius/finance/revenue"' not in html
    assert 'href="/admin/radius/finance/debts"' not in html
    assert 'href="/admin/radius/finance/loans"' not in html


def test_finance_center_hub_get_writes_nothing(app):
    before = _counts(app)
    with app.test_client() as client:
        _auth(client)
        for tab in ("dashboard", "wallets", "revenue", "loans_debts"):
            client.get(f"{_HUB}?tab={tab}")
    assert _counts(app) == before
